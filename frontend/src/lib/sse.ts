type SSEHandler = (event: MessageEvent) => void;

export class SSEManager {
  private source: EventSource | null = null;
  private handlers = new Map<string, Set<SSEHandler>>();
  private url: string;

  constructor(path: string) {
    this.url = path;
  }

  connect(): void {
    this.source = new EventSource(this.url);
    this.source.onmessage = (e) => {
      this.handlers.get("message")?.forEach((h) => h(e));
    };
    this.source.onerror = () => {
      // EventSource auto-reconnects
    };
  }

  on(eventType: string, handler: SSEHandler): () => void {
    if (!this.handlers.has(eventType)) this.handlers.set(eventType, new Set());
    this.handlers.get(eventType)!.add(handler);
    this.source?.addEventListener(eventType, handler);
    return () => {
      this.handlers.get(eventType)?.delete(handler);
      this.source?.removeEventListener(eventType, handler);
    };
  }

  disconnect(): void { this.source?.close(); this.source = null; }
}
