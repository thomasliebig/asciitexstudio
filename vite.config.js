import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
export default defineConfig({
    base: '/asciitexstudio/',
    plugins: [vue()],
    worker: { format: 'es' },
});
