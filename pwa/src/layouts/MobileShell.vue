<script setup>
import AppHeader from '../components/AppHeader.vue'
import BottomNav from '../components/BottomNav.vue'
</script>

<template>
  <div class="app">
    <AppHeader />
    <main class="content">
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
    <BottomNav />
  </div>
</template>
