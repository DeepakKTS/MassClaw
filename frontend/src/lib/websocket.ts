type MessageHandler = (data: any) => void;

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers = new Set<MessageHandler>();
  private reconnectDelay = 1000;
  private maxDelay = 30000;
  private shouldReconnect = true;

  constructor(path: string) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${protocol}//${window.location.host}${path}`;
  }

  connect(): void {
    this.shouldReconnect = true;
    this._connect();
  }

  private _connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => { this.reconnectDelay = 1000; };
    this.ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        this.handlers.forEach((h) => h(data));
      } catch {}
    };
    this.ws.onclose = () => {
      if (this.shouldReconnect) {
        setTimeout(() => this._connect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxDelay);
      }
    };
    this.ws.onerror = () => this.ws?.close();
  }

  send(data: object): void { this.ws?.send(JSON.stringify(data)); }
  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }
  disconnect(): void { this.shouldReconnect = false; this.ws?.close(); }
  get connected(): boolean { return this.ws?.readyState === WebSocket.OPEN; }
}
