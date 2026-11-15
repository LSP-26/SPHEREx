/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API when it is on another host than the page. Default: same host. */
  readonly VITE_API_BASE?: string;
}
