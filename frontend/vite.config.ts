import {defineConfig} from 'vite'; import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':'http://localhost:8000'}},test:{environment:'jsdom',environmentOptions:{jsdom:{url:'http://localhost:5173'}},setupFiles:'./src/test-setup.ts'}});
