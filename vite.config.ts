import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_');

  return {
    base: normalizeBasePath(env.VITE_BASE_PATH),
    build: {
      target: 'es2022',
      sourcemap: false,
    },
    server: {
      host: '127.0.0.1',
      port: 5174,
    },
  };
});

function normalizeBasePath(value: string | undefined): string {
  const path = value?.trim().replace(/^\/+|\/+$/g, '');
  return path ? `/${path}/` : '/';
}
