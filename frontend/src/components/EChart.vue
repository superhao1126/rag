<script setup lang="ts">
/**
 * 通用 ECharts 包装组件
 * - 按需引入 BarChart / LineChart + 常用组件，避免打包整个 echarts
 * - 支持容器尺寸变化自适应（ResizeObserver）
 * - 监听 <html data-theme> 切换时重绘（颜色由调用方 option 控制，这里只负责触发刷新）
 * - 卸载时自动 dispose 释放 canvas
 *
 * 使用：
 *   <EChart :option="option" style="height:240px" />
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

const props = defineProps<{
  option: EChartsOption;
  loading?: boolean;
}>();

const containerRef = ref<HTMLDivElement | null>(null);
const chartRef = shallowRef<echarts.ECharts | null>(null);
let resizeObserver: ResizeObserver | null = null;
let themeObserver: MutationObserver | null = null;

function applyOption() {
  if (!chartRef.value) return;
  chartRef.value.setOption(props.option, { notMerge: true });
}

function applyLoading() {
  if (!chartRef.value) return;
  if (props.loading) {
    chartRef.value.showLoading("default", {
      text: "",
      maskColor: "rgba(255,255,255,0)",
      spinnerRadius: 10,
      lineWidth: 2,
    });
  } else {
    chartRef.value.hideLoading();
  }
}

onMounted(() => {
  if (!containerRef.value) return;
  chartRef.value = echarts.init(containerRef.value, undefined, { renderer: "canvas" });
  applyOption();
  applyLoading();

  resizeObserver = new ResizeObserver(() => {
    chartRef.value?.resize();
  });
  resizeObserver.observe(containerRef.value);

  themeObserver = new MutationObserver(() => {
    applyOption();
    chartRef.value?.resize();
  });
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
});

watch(() => props.option, applyOption, { deep: true });
watch(() => props.loading, applyLoading);

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  themeObserver?.disconnect();
  chartRef.value?.dispose();
  chartRef.value = null;
});
</script>

<template>
  <div ref="containerRef" class="echart-container"></div>
</template>

<style scoped>
.echart-container {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
