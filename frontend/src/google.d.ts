export {};
declare global {
  interface Window {
    gapi?: {
      load(name: string, options: { callback(): void; onerror(): void }): void;
    };
    google?: {
      accounts?: {
        id?: { initialize(options: unknown): void; renderButton(element: HTMLElement, options: unknown): void };
        oauth2?: {
          initTokenClient(options: {
            client_id: string;
            scope: string;
            callback(response: { access_token?: string; error?: string; error_description?: string }): void;
          }): { requestAccessToken(options?: { prompt?: string; hint?: string }): void };
        };
      };
      picker?: {
        Action: { PICKED: string; CANCEL: string };
        Response: { ACTION: string; DOCUMENTS: string };
        Document: { ID: string; NAME: string; MIME_TYPE: string };
        ViewId: { DOCS: string };
        DocsViewMode: { LIST: string };
        DocsView: new (viewId: string) => {
          setIncludeFolders(value: boolean): unknown;
          setSelectFolderEnabled(value: boolean): unknown;
          setMimeTypes(value: string): unknown;
          setMode(value: string): unknown;
        };
        PickerBuilder: new () => {
          addView(view: unknown): unknown;
          setAppId(value: string): unknown;
          setOAuthToken(value: string): unknown;
          setDeveloperKey(value: string): unknown;
          setOrigin(value: string): unknown;
          setCallback(value: (data: Record<string, unknown>) => void): unknown;
          setTitle(value: string): unknown;
          build(): { setVisible(value: boolean): void };
        };
      };
    };
  }
}
