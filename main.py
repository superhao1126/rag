"""
RAG 知识问答系统 — CLI 入口
用法: python main.py --help
"""

import os
import shutil
import argparse

from core.config import Config
from core.pipeline import RAGPipeline


def main():
    parser = argparse.ArgumentParser(
        description="RAG 知识问答系统 — 基于阿里云百炼大模型 + BM25 混合检索",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py --files doc1.txt doc2.pdf --interactive
  python main.py --dir ./documents --interactive
  python main.py --files doc.txt -q "主要内容是什么？"
  python main.py --files doc.txt -q "关键词查询" --mode bm25
  python main.py --cleanup --temp-dir /tmp/my_rag

环境变量:
  DASHSCOPE_API_KEY  阿里云百炼 API Key（必填）
        """,
    )

    parser.add_argument("--files", nargs="+", help="要添加的文件路径（txt/md/pdf）")
    parser.add_argument("--dir", help="要添加的目录路径")
    parser.add_argument("--query", "-q", help="直接提问")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互式问答")
    parser.add_argument("--api-key", help="百炼 API Key")
    parser.add_argument("--chunk-size", type=int, default=500, help="分块大小（默认 500）")
    parser.add_argument("--chunk-overlap", type=int, default=100, help="分块重叠（默认 100）")
    parser.add_argument("--top-k", type=int, default=3, help="检索片段数（默认 3）")
    parser.add_argument("--temp-dir", help="临时存储目录")
    parser.add_argument("--mode", choices=["hybrid", "vector", "bm25"], default="hybrid",
                        help="检索模式（默认 hybrid）")
    parser.add_argument("--stream", action="store_true", default=True, help="流式输出（默认开启）")
    parser.add_argument("--no-stream", dest="stream", action="store_false", help="禁用流式")
    parser.add_argument("--history-turns", type=int, default=5, help="对话记忆轮数（默认 5）")
    parser.add_argument("--cleanup", action="store_true", help="清理临时目录")

    args = parser.parse_args()

    # 清理模式
    if args.cleanup:
        if args.temp_dir and os.path.exists(args.temp_dir):
            shutil.rmtree(args.temp_dir)
            print(f"🗑️ 已清理: {args.temp_dir}")
        else:
            print("请通过 --temp-dir 指定目录。")
        return

    if not args.files and not args.dir and not args.query and not args.interactive:
        parser.print_help()
        return

    # 构建配置
    config = Config()
    config.CHUNK_SIZE = args.chunk_size
    config.CHUNK_OVERLAP = args.chunk_overlap
    config.TOP_K = args.top_k
    config.TEMP_DIR = args.temp_dir
    config.STREAM = args.stream
    config.HISTORY_TURNS = args.history_turns

    rag = RAGPipeline(api_key=args.api_key, config=config)

    try:
        if args.files:
            for f in args.files:
                if os.path.exists(f):
                    rag.add_file(f)
                else:
                    print(f"⚠️ 文件不存在: {f}")

        if args.dir:
            if os.path.isdir(args.dir):
                rag.add_directory(args.dir)
            else:
                print(f"⚠️ 目录不存在: {args.dir}")

        if args.query:
            answer = rag.query(args.query, mode=args.mode)
            if not config.STREAM:
                print(f"\n💬 回答:\n{answer}")

        if args.interactive:
            rag.interactive()

    except KeyboardInterrupt:
        print("\n操作已取消。")
    except Exception as e:
        print(f"❌ 错误: {e}")
        raise


if __name__ == "__main__":
    main()
