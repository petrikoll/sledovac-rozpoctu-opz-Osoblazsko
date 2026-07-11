export {};
declare global {
  interface Window {
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
    };
  }
}
