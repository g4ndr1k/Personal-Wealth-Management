/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APPROVAL_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
