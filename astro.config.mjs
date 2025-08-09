import { defineConfig } from 'astro/config';

export default defineConfig({
  
  // https://astro.build/config
  output: 'static',
  site: 'https://yongfang190.github.io', // ✅ 填完整 URL
  // base: '/'  // 用户主页不需要改 base
});
