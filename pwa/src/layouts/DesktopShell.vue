<script setup>
import DesktopSidebar from '../components/DesktopSidebar.vue'
</script>

<template>
  <div class="desktop-shell">
    <DesktopSidebar />
    <main class="desktop-content">
      <RouterView v-slot="{ Component, route }">
        <KeepAlive>
          <component
            :is="Component"
            v-if="route.meta?.keepAlive"
            :key="route.name || route.path"
          />
        </KeepAlive>
        <component
          :is="Component"
          v-if="!route.meta?.keepAlive"
          :key="route.fullPath"
        />
      </RouterView>
    </main>
  </div>
</template>
