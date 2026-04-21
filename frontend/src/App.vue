<script setup lang="ts">
import { watch, onMounted } from "vue";
import { RouterView } from "vue-router";
import AppHeader from "@/components/AppHeader.vue";
import { useAppStore } from "@/stores/session";

const store = useAppStore();

function applyTheme(dark: boolean) {
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

onMounted(() => applyTheme(store.isDark));
watch(() => store.isDark, applyTheme);
</script>

<template>
  <div class="app-layout">
    <AppHeader />
    <div class="app-content">
      <RouterView />
    </div>
  </div>
</template>

<style scoped>
.app-layout {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-app);
  transition: background 0.25s ease;
}

.app-content {
  display: flex;
  flex: 1;
  overflow: hidden;
  background: var(--bg-app);
  transition: background 0.25s ease;
}
</style>
