"""
RAG 主流程 —— 串联文件读取、分块、向量化、BM25 索引、混合检索、大模型生成。
全局单例模式：所有文件共用同一个知识库，对话历史持久化到 PostgreSQL。
"""

import os
import sys
import tempfile
from typing import List, Dict, Optional, Generator, Callable

from openai import OpenAI

from core.config import Config
from core.reader import FileReader
from core.chunker import TextChunker
from core.vectorstore import VectorStore
from core.bm25 import BM25Index
from core.retriever import HybridRetriever
from core.llm_stats import llm_stats


class RAGPipeline:
    """RAG 流程主类（全局单例，无会话隔离）"""

    def __init__(self, api_key: str = None, config: Config = None):
        self.config = config or Config()
        self._use_pg = self.config.USE_PG

        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "请设置 DASHSCOPE_API_KEY 环境变量，或在初始化时传入 api_key 参数。\n"
                "获取方式：https://bailian.console.aliyun.com/"
            )

        self.client = OpenAI(api_key=self.api_key, base_url=self.config.BASE_URL)

        # 临时目录：用于暂存上传文件
        if self.config.TEMP_DIR:
            self.temp_dir = self.config.TEMP_DIR
            os.makedirs(self.temp_dir, exist_ok=True)
        else:
            self.temp_dir = tempfile.mkdtemp(prefix="rag_store_")
        print(f"📁 临时存储目录: {self.temp_dir}")

        self.chunker = TextChunker(
            chunk_size=self.config.CHUNK_SIZE,
            chunk_overlap=self.config.CHUNK_OVERLAP,
        )

        if self._use_pg:
            from core.pg_vectorstore import PgVectorStore
            self.vector_store = PgVectorStore(
                client=self.client,
                db_config=self.config.PG_CONFIG,
                db_uri=self.config.PG_URI or None,
                embedding_model=self.config.EMBEDDING_MODEL,
                embedding_dimension=self.config.EMBEDDING_DIMENSION,
            )
            print("💾 向量存储: PostgreSQL + pgvector")
            # 将 token 调用流水持久化到 PG
            llm_stats.attach_storage(self.vector_store)
        else:
            self.vector_store = VectorStore(
                client=self.client,
                storage_dir=self.temp_dir,
                embedding_model=self.config.EMBEDDING_MODEL,
            )

        self.bm25_index = BM25Index()

        if self.vector_store.metadata:
            if self._use_pg:
                # 始终重建 BM25 索引（确保使用最新分词器）
                print("  重建 BM25 索引（改进分词器）...")
                self.bm25_index.fit(self.vector_store.metadata)
                self.vector_store.save_bm25(self.bm25_index.to_dict())
            else:
                loaded = self.bm25_index.load(self.temp_dir, self.vector_store.metadata)
                if not loaded:
                    self.bm25_index.fit(self.vector_store.metadata)
                    self.bm25_index.save(self.temp_dir)

        self.retriever = HybridRetriever(
            vector_store=self.vector_store,
            bm25_index=self.bm25_index,
            vector_weight=self.config.VECTOR_WEIGHT,
            bm25_weight=self.config.BM25_WEIGHT,
            rrf_k=self.config.RRF_K,
        )

        # 分块可视化缓存：{source_name: visualization_data}
        self._chunk_viz_cache: Dict[str, Dict] = {}

    # ---- 知识库管理 ----

    def add_file(self, file_path: str):
        """添加文件到知识库（全库共享）"""
        print(f"\n📄 正在处理文件: {file_path}")
        text = FileReader.read(file_path)

        if not text.strip():
            print("  ⚠️ 文件内容为空，跳过。")
            return

        source_name = os.path.basename(file_path)
        print(f"  文件内容长度: {len(text)} 字符")

        chunks = self.chunker.chunk(text, source=source_name)
        print(f"  分块完成: {len(chunks)} 个文本块")

        self._chunk_viz_cache[source_name] = self.chunker.get_visualization_data(text, chunks)

        self.vector_store.add(chunks)

        self.bm25_index.fit(self.vector_store.metadata)
        if self._use_pg:
            self.vector_store.save_bm25(self.bm25_index.to_dict())
        else:
            self.bm25_index.save(self.temp_dir)

    def add_file_with_progress(self, file_path: str, on_progress: Callable[[dict], None]):
        """添加文件到知识库，通过回调同步上报进度（供 SSE 流使用）。"""
        source_name = os.path.basename(file_path)

        on_progress({"type": "progress", "stage": "reading",
                     "message": "正在读取文件…", "percent": 5})
        text = FileReader.read(file_path)

        if not text.strip():
            on_progress({"type": "error",
                         "message": f"文件内容为空（可能是扫描版 PDF）：{source_name}"})
            return

        on_progress({"type": "progress", "stage": "reading",
                     "message": f"读取完成 ({len(text):,} 字符)", "percent": 15})

        on_progress({"type": "progress", "stage": "chunking",
                     "message": "正在智能分块…", "percent": 20})
        chunks = self.chunker.chunk(text, source=source_name)
        self._chunk_viz_cache[source_name] = self.chunker.get_visualization_data(text, chunks)
        on_progress({"type": "progress", "stage": "chunking",
                     "message": f"分块完成（{len(chunks)} 个文本块）", "percent": 30})

        total_batches = max(1, (len(chunks) + 9) // 10)

        def embed_cb(current: int, total: int):
            pct = 30 + int(current / total * 50)
            on_progress({"type": "progress", "stage": "embedding",
                         "message": f"向量化 {current}/{total} 批…", "percent": pct})

        on_progress({"type": "progress", "stage": "embedding",
                     "message": "开始向量化…", "percent": 32})
        self.vector_store.add(chunks, on_progress=embed_cb)

        on_progress({"type": "progress", "stage": "indexing",
                     "message": "建立检索索引…", "percent": 88})
        self.bm25_index.fit(self.vector_store.metadata)
        if self._use_pg:
            self.vector_store.save_bm25(self.bm25_index.to_dict())
        else:
            self.bm25_index.save(self.temp_dir)

        on_progress({"type": "done", "source": source_name,
                     "chunk_count": len(chunks), "total_chars": len(text)})

    def add_directory(self, dir_path: str, recursive: bool = True):
        """批量添加目录下所有支持格式的文件"""
        from pathlib import Path

        path = Path(dir_path)
        pattern_func = path.rglob if recursive else path.glob
        files = sorted(f for f in pattern_func("*") if f.suffix.lower() in FileReader.SUPPORTED_EXTENSIONS)

        if not files:
            print(f"目录 {dir_path} 下未找到支持的文件。")
            return

        print(f"📂 找到 {len(files)} 个文件，开始处理...")
        for f in files:
            try:
                self.add_file(str(f))
            except Exception as e:
                print(f"  ❌ 处理文件 {f} 时出错: {e}")

    # ---- 检索问答（带对话历史持久化）----

    def _generate_title(self, question: str, answer: str) -> str:
        """调用 LLM 为对话生成一个简洁的中文标题（≤15字）"""
        resp = self.client.chat.completions.create(
            model=self.config.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个对话标题生成助手。根据用户问题和AI回答，"
                        "提炼一个简洁的中文对话标题，不超过15个字，不加引号，不带标点。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"用户问题：{question[:200]}\n\n"
                        f"AI回答摘要：{answer[:300]}\n\n"
                        "请生成标题："
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=30,
            stream=False,
        )
        raw = resp.choices[0].message.content or ""
        try:
            usage = getattr(resp, "usage", None)
            if usage:
                llm_stats.record(
                    model=self.config.LLM_MODEL,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    total_tokens=getattr(usage, "total_tokens", None),
                    call_type="title",
                )
        except Exception:
            pass
        title = raw.strip().strip('"\'《》「」【】').strip()
        return title[:20] if title else question[:20]

    def _build_messages(self, question: str, results: List[Dict], history: List[Dict]) -> List[Dict]:
        """构建 LLM 消息列表"""
        context_parts = [
            f"【片段{i+1}】(来源: {r['source']})\n{r['text']}"
            for i, r in enumerate(results)
        ]
        context = "\n\n".join(context_parts)

        system_prompt = (
            "你是一个专业的中文知识问答助手。请根据以下参考资料回答用户的问题。\n"
            "要求：\n"
            "1. 全程使用中文（包括内部思考过程也必须使用中文，禁止使用英文推理）\n"
            "2. 尽量基于参考资料中的内容来回答\n"
            "3. 如果参考资料中没有相关信息，请如实说明\n"
            "4. 回答要准确、简洁、有条理\n"
            "5. **必须**在每个使用了参考资料的语句末尾，用 [n] 形式标注引用编号"
            "（n 对应【片段n】，多个用 [1][2] 拼接，不要写成 [1,2]）；"
            "未使用参考资料的内容不要添加引用标注\n\n"
            f"参考资料：\n{context}"
        )

        messages: List[Dict] = [{"role": "system", "content": system_prompt}]
        if self.config.HISTORY_TURNS > 0 and history:
            recent = history[-(self.config.HISTORY_TURNS * 2):]
            messages.extend(recent)
        messages.append({"role": "user", "content": question})
        return messages

    def query_with_conversation(
        self,
        question: str,
        conversation_id: str,
        mode: str = "hybrid",
        top_k: int = None,
        enable_thinking: bool = False,
        regenerate: bool = False,
    ) -> Generator[str, None, None]:
        """
        流式问答（支持对话持久化）。
        从 DB 加载历史消息，完成后将问题和回答写回 DB。

        Yields: SSE 格式字符串
        """
        import time

        k = top_k or self.config.TOP_K
        t_total_start = time.perf_counter()

        # 检索
        print(f"\n🔍 [Q] {question[:80]}{'...' if len(question) > 80 else ''}")
        print(f"🔍 正在检索（模式: {mode}, top_k={k}）...")
        t_retrieval_start = time.perf_counter()
        results = self.retriever.search(question, top_k=k, mode=mode)
        t_retrieval = time.perf_counter() - t_retrieval_start
        print(f"   ⏱️  检索耗时: {t_retrieval*1000:.0f} ms, 命中 {len(results)} 条")

        if not results:
            yield "data: {\"type\": \"error\", \"message\": \"知识库为空，请先上传文件。\"}\n\n"
            return

        # 打印检索摘要
        for i, r in enumerate(results):
            score_info = []
            if "similarity" in r:
                score_info.append(f"向量: {r['similarity']:.4f}")
            if "bm25_score" in r:
                score_info.append(f"BM25: {r['bm25_score']:.4f}")
            if "rrf_score" in r:
                score_info.append(f"RRF: {r['rrf_score']:.6f}")
            print(f"    [{i+1}] {' | '.join(score_info)} | 来源: {r['source']}")

        # 从 DB 加载对话历史（重新生成时先删掉最后一条助手回复，避免污染上下文）
        if self._use_pg and regenerate:
            self.vector_store.delete_last_assistant_message(conversation_id)

        history: List[Dict] = []
        if self._use_pg:
            raw = self.vector_store.get_conversation_messages(conversation_id)
            history = [{"role": m["role"], "content": m["content"]} for m in raw]
            # 重新生成时，最后一条用户消息已存在，不再重复入库
            if regenerate and history and history[-1]["role"] == "user":
                history = history[:-1]  # 当前 question 通过 _build_messages 单独加入

        messages = self._build_messages(question, results, history)

        # 估算 prompt 体量
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        print(
            f"   📦 历史轮次: {len(history)//2}, 消息数: {len(messages)}, "
            f"prompt 字符: {prompt_chars}, regenerate={regenerate}"
        )

        # 将用户消息存入 DB（重新生成时跳过）
        if self._use_pg and not regenerate:
            self.vector_store.add_message(conversation_id, "user", question)

        if self._use_pg:
            # 第一条消息时，用占位标题，待 LLM 回复后用 AI 来命名
            is_first_message = not history and not regenerate

        # 构造检索信息（用于前端展示）
        retrieval_info = {
            "mode": mode,
            "results": [
                {
                    "source": r["source"],
                    "chunk_index": r.get("chunk_index", 0),
                    "similarity": r.get("similarity"),
                    "bm25_score": r.get("bm25_score"),
                    "rrf_score": r.get("rrf_score"),
                    "text_preview": r["text"],
                }
                for r in results
            ],
        }

        import json

        # 先发送检索信息
        yield f"data: {json.dumps({'type': 'retrieval', 'data': retrieval_info}, ensure_ascii=False)}\n\n"

        # 流式生成回答
        print(f"   🤖 调用 LLM: model={self.config.LLM_MODEL}, stream=True, thinking={enable_thinking}")
        t_llm_start = time.perf_counter()
        resp = self.client.chat.completions.create(
            model=self.config.LLM_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"enable_thinking": bool(enable_thinking)},
        )

        collected: List[str] = []
        thinking_parts: List[str] = []
        chunk_count = 0
        t_first_token = None
        t_first_thinking = None
        last_usage = None
        for chunk in resp:
            if getattr(chunk, "usage", None):
                last_usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # qwen3 thinking 增量在 reasoning_content 字段
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                if t_first_thinking is None:
                    t_first_thinking = time.perf_counter() - t_llm_start
                    print(f"   💭 思考首字: {t_first_thinking*1000:.0f} ms")
                thinking_parts.append(reasoning)
                yield f"data: {json.dumps({'type': 'thinking', 'content': reasoning}, ensure_ascii=False)}\n\n"
            if delta.content:
                if t_first_token is None:
                    t_first_token = time.perf_counter() - t_llm_start
                    print(f"   ⚡ 首字耗时 (TTFT): {t_first_token*1000:.0f} ms")
                collected.append(delta.content)
                chunk_count += 1
                yield f"data: {json.dumps({'type': 'content', 'content': delta.content}, ensure_ascii=False)}\n\n"

        t_llm_total = time.perf_counter() - t_llm_start
        answer = "".join(collected)
        thinking_text = "".join(thinking_parts)
        ans_chars = len(answer)
        tps = (ans_chars / t_llm_total) if t_llm_total > 0 else 0
        print(
            f"   ✅ LLM 完成: 思考 {len(thinking_text)} 字符 + 回答 {ans_chars} 字符 / "
            f"{chunk_count} chunks, 总耗时 {t_llm_total:.2f}s, 速度 {tps:.1f} 字符/s"
        )

        # 记录 token 用量并通过 SSE 推送给前端
        usage_payload = None
        if last_usage:
            prompt_tokens = getattr(last_usage, "prompt_tokens", 0) or 0
            completion_tokens = getattr(last_usage, "completion_tokens", 0) or 0
            total_tokens = getattr(last_usage, "total_tokens", None)
            try:
                llm_stats.record(
                    model=self.config.LLM_MODEL,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    call_type="chat",
                )
            except Exception:
                pass
            usage_payload = {
                "model": self.config.LLM_MODEL,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens if total_tokens is not None else (prompt_tokens + completion_tokens),
            }
            print(
                f"   📊 token 用量: prompt={prompt_tokens}, completion={completion_tokens}, "
                f"total={usage_payload['total_tokens']}"
            )
            yield f"data: {json.dumps({'type': 'usage', 'usage': usage_payload, 'totals': llm_stats.snapshot(self.config.LLM_MODEL)}, ensure_ascii=False)}\n\n"

        # 将助手回复存入 DB
        new_message_id = None
        if self._use_pg:
            t_db_start = time.perf_counter()
            saved_info = dict(retrieval_info)
            if thinking_text:
                saved_info["thinking"] = thinking_text
            new_message_id = self.vector_store.add_message(
                conversation_id, "assistant", answer, saved_info
            )
            # 第一条消息时，用 LLM 自动生成一个简洁的对话标题
            if is_first_message:
                try:
                    t_title_start = time.perf_counter()
                    title = self._generate_title(question, answer)
                    print(f"   🏷️  标题生成: {(time.perf_counter()-t_title_start)*1000:.0f} ms → {title!r}")
                except Exception as e:
                    title = question[:30] + ("..." if len(question) > 30 else "")
                    print(f"   ⚠️  标题生成失败: {e}, 退化为问题前缀")
                self.vector_store.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title', 'title': title}, ensure_ascii=False)}\n\n"
            print(f"   💾 DB 写入耗时: {(time.perf_counter()-t_db_start)*1000:.0f} ms")

        t_total = time.perf_counter() - t_total_start
        print(
            f"🏁 query 完成: 总 {t_total:.2f}s "
            f"(检索 {t_retrieval*1000:.0f}ms + LLM {t_llm_total:.2f}s)"
        )

        yield f"data: {json.dumps({'type': 'done', 'message_id': new_message_id}, ensure_ascii=False)}\n\n"

    def query(self, question: str, top_k: int = None, stream: bool = None, mode: str = "hybrid") -> str:
        """非持久化问答（保留向后兼容，用于命令行模式）"""
        k = top_k or self.config.TOP_K
        use_stream = stream if stream is not None else self.config.STREAM

        print(f"\n🔍 正在检索（模式: {mode}）...")
        results = self.retriever.search(question, top_k=k, mode=mode)

        if not results:
            return "❌ 知识库为空，请先添加文件。"

        messages = self._build_messages(question, results, [])

        if use_stream:
            return self._stream_generate(messages)
        else:
            print("🤖 正在生成回答...")
            resp = self.client.chat.completions.create(
                model=self.config.LLM_MODEL,
                messages=messages,
                temperature=0.7,
                max_tokens=2000,
            )
            try:
                usage = getattr(resp, "usage", None)
                if usage:
                    llm_stats.record(
                        model=self.config.LLM_MODEL,
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(usage, "total_tokens", None),
                        call_type="chat",
                    )
            except Exception:
                pass
            return resp.choices[0].message.content

    def _stream_generate(self, messages: List[Dict]) -> str:
        print("🤖 大模型回答（流式）:")
        resp = self.client.chat.completions.create(
            model=self.config.LLM_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=2000,
            stream=True,
        )
        collected = []
        for chunk in resp:
            delta = chunk.choices[0].delta
            if delta.content:
                sys.stdout.write(delta.content)
                sys.stdout.flush()
                collected.append(delta.content)
        print()
        return "".join(collected)

    # ---- 工具方法 ----

    def get_stats(self) -> Dict:
        stats = self.vector_store.get_stats()
        stats["bm25_documents"] = self.bm25_index.N
        stats["bm25_vocabulary"] = len(self.bm25_index.df)
        return stats

    def get_chunk_visualization(self, source: str = None) -> Dict:
        if source:
            return self._chunk_viz_cache.get(source, {})
        return self._chunk_viz_cache

    # ---- 交互模式（命令行）----

    def interactive(self):
        print("\n" + "=" * 60)
        print("  📚 RAG 知识问答系统 — 交互模式")
        print("=" * 60)

        stats = self.get_stats()
        print(f"  知识库: {stats['total_chunks']} 个文本块")
        print(f"  BM25:  {stats['bm25_vocabulary']} 个唯一词")
        sources = stats["sources"]
        print(f"  来源:  {', '.join(sources) if sources else '（空）'}")
        stream_s = "✅ 开启" if self.config.STREAM else "❌ 关闭"
        print(f"  流式:  {stream_s}  |  记忆: 最近 {self.config.HISTORY_TURNS} 轮")
        print("-" * 60)
        print("  输入问题提问 | mode:hybrid/vector/bm25 切换检索模式")
        print("  stream 切换流式 | stats 状态 | quit 退出")
        print("-" * 60)

        search_mode = "hybrid"

        while True:
            try:
                question = input(f"\n📝 [{search_mode}] 你的问题: ").strip()
                if not question:
                    continue
                if question.lower() in ("quit", "exit", "q"):
                    print("👋 再见！")
                    break
                if question.lower() == "stats":
                    for k, v in self.get_stats().items():
                        print(f"  {k}: {v}")
                    continue
                if question.lower() == "stream":
                    self.config.STREAM = not self.config.STREAM
                    print(f"  流式输出: {'✅ 开启' if self.config.STREAM else '❌ 关闭'}")
                    continue
                if question.lower().startswith("mode:"):
                    m = question.split(":", 1)[1].strip()
                    if m in ("hybrid", "vector", "bm25"):
                        search_mode = m
                        print(f"  检索模式已切换为: {search_mode}")
                    else:
                        print("  可选模式: hybrid / vector / bm25")
                    continue

                answer = self.query(question, mode=search_mode)
                if not self.config.STREAM:
                    print(f"\n💬 回答:\n{answer}")

            except KeyboardInterrupt:
                print("\n👋 再见！")
                break
            except Exception as e:
                print(f"❌ 出错: {e}")
