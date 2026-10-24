// Player de vídeo scrcpy 4.1 para o painel.
//
// Recebe o stream H.264 cru encaminhado pelo backend em /ws/stream/{serial},
// interpreta-o com a implementação de referência (@yume-chan/scrcpy,
// ScrcpyOptions4_1) e descodifica com WebCodecs.
//
// É empacotado em /static/scrcpy-bundle.js (npm run build) e fica disponível
// como `window.ScrcpyPanel` — nada é carregado de CDNs.
//
// As opções abaixo TÊM de coincidir com os argumentos do servidor em
// server/mirror_manager.py (send_device_meta=false, send_stream_meta=true,
// send_frame_meta=true, video_codec=h264). O dummy byte é consumido pelo
// backend e nunca chega aqui.

import { ScrcpyOptions4_1 } from "@yume-chan/scrcpy";
import {
  WebCodecsVideoDecoder,
  BitmapVideoFrameRenderer,
} from "@yume-chan/scrcpy-decoder-webcodecs";
import { ReadableStream } from "@yume-chan/stream-extra";

const RECONNECT_MS = 3000;
const AUTH_CLOSE_CODE = 4401;

/** Decoder real (WebCodecs). Substituível nos testes via `createDecoder`. */
function defaultCreateDecoder(codec) {
  if (!WebCodecsVideoDecoder.isSupported) {
    throw new Error(
      "WebCodecs indisponível. Abra o painel em http://127.0.0.1:8000 ou " +
      "http://localhost:8000 (contexto seguro) com Chrome/Edge/Firefox recente.");
  }
  const renderer = new BitmapVideoFrameRenderer();
  const decoder = new WebCodecsVideoDecoder({
    codec,
    renderer,
    optimizeForLatency: true,
  });
  return {
    canvas: renderer.canvas,
    writable: decoder.writable,
    dispose() { decoder.dispose(); },
  };
}

/**
 * Converte um clique (clientX/Y) em coordenadas de pixel DO VÍDEO.
 * Considera as faixas pretas (letterbox) do `object-fit: contain`.
 * Devolve null se o clique cair fora da imagem.
 */
export function mapClick(rect, videoW, videoH, clientX, clientY) {
  if (!videoW || !videoH || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / videoW, rect.height / videoH);
  const dw = videoW * scale;
  const dh = videoH * scale;
  const ox = rect.left + (rect.width - dw) / 2;
  const oy = rect.top + (rect.height - dh) / 2;
  const x = Math.floor((clientX - ox) / scale);
  const y = Math.floor((clientY - oy) / scale);
  if (x < 0 || y < 0 || x >= videoW || y >= videoH) return null;
  return { x, y };
}

export class ScreenPlayer {
  /**
   * @param {HTMLElement} container onde o <canvas> é inserido
   * @param {object} opts
   *   getToken():string        token atual
   *   onStatus(msg)            texto para mostrar ao utilizador
   *   onAuthError():Promise    chamado quando o servidor recusa o token
   *   createDecoder(codec)     (testes) devolve {canvas, writable, dispose}
   *   reconnectMs              atraso entre tentativas
   */
  constructor(container, opts = {}) {
    this.container = container;
    this.getToken = opts.getToken || (() => "");
    this.onStatus = opts.onStatus || (() => {});
    this.onAuthError = opts.onAuthError || (async () => false);
    this.createDecoder = opts.createDecoder || defaultCreateDecoder;
    this.reconnectMs = opts.reconnectMs ?? RECONNECT_MS;

    this.canvas = null;
    this._serial = null;
    this._gen = 0;        // invalida callbacks de ligações antigas
    this._ws = null;
    this._view = null;
    this._timer = null;
    this._fatal = false;
  }

  connect(serial) {
    this._serial = serial;
    this._fatal = false;
    this._restart();
  }

  disconnect() {
    this._serial = null;
    this._teardown();
    this.onStatus("sem stream");
  }

  _restart() {
    this._teardown();
    this._open(this._gen);
  }

  _teardown() {
    this._gen++;   // qualquer onclose/pipeline antigo passa a ser ignorado
    clearTimeout(this._timer);

    const ws = this._ws;
    this._ws = null;
    if (ws) {
      // Desligar handlers ANTES de fechar: o `close()` dispara `onclose`
      // e a versão antiga reagendava uma reconexão por cima da nova ligação
      // (loop de abrir/fechar de 3 em 3 s).
      ws.onmessage = ws.onclose = ws.onerror = null;
      try { ws.close(); } catch { /* já fechado */ }
    }

    const view = this._view;
    this._view = null;
    if (view) {
      try { view.dispose(); } catch { /* ignore */ }
      if (view.canvas && view.canvas.parentNode) {
        view.canvas.parentNode.removeChild(view.canvas);
      }
    }
    this.canvas = null;
  }

  _schedule(gen, ms) {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      if (gen === this._gen && this._serial && !this._fatal) this._restart();
    }, ms);
  }

  _onClosed(gen, ev) {
    if (gen !== this._gen || this._fatal) return;
    const reason = ev && ev.reason ? ev.reason : "ligação terminada";
    if (ev && ev.code === AUTH_CLOSE_CODE) {
      this.onStatus("sessão expirada — a autenticar de novo…");
      Promise.resolve(this.onAuthError())
        .catch(() => false)
        .then(() => this._schedule(gen, 0));
      return;
    }
    this.onStatus(
      `${reason} — nova tentativa em ${Math.round(this.reconnectMs / 1000)}s`);
    this._schedule(gen, this.reconnectMs);
  }

  async _open(gen) {
    const serial = this._serial;
    if (!serial) return;

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/stream/` +
      `${encodeURIComponent(serial)}?token=${encodeURIComponent(this.getToken())}`;

    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this._ws = ws;
    this.onStatus(`a ligar a ${serial}…`);

    const options = new ScrcpyOptions4_1({
      sendDeviceMeta: false,
      sendStreamMeta: true,
      sendFrameMeta: true,
      videoCodec: "h264",
    });

    const source = new ReadableStream({
      start: (controller) => {
        ws.onmessage = (e) => {
          try { controller.enqueue(new Uint8Array(e.data)); } catch { /* fechado */ }
        };
        ws.onclose = (e) => {
          try { controller.close(); } catch { /* já fechado */ }
          this._onClosed(gen, e);
        };
        ws.onerror = () => { /* o onclose trata de tudo */ };
      },
      cancel: () => { try { ws.close(); } catch { /* ignore */ } },
    });

    try {
      // Só resolve quando o servidor começa a emitir (codec id).
      const { metadata, stream } = await options.parseVideoStreamMetadata(source);
      if (gen !== this._gen) return;

      let view;
      try {
        view = this.createDecoder(metadata.codec);
      } catch (e) {
        // Falha permanente (ex.: sem WebCodecs): não vale a pena reconectar.
        this._fatal = true;
        this.onStatus(`Erro: ${e.message || e}`);
        try { ws.close(); } catch { /* ignore */ }
        return;
      }
      this._view = view;
      this.canvas = view.canvas;
      view.canvas.id = "scrcpy-canvas";
      this.container.appendChild(view.canvas);
      this.onStatus(`stream ativo: ${serial}`);

      await stream
        .pipeThrough(options.createMediaStreamTransformer())
        .pipeTo(view.writable);
    } catch (e) {
      // Fim normal do stream (ws fechado) não é erro.
      if (gen === this._gen && !/ExactReadable ended/.test(String(e))) {
        this.onStatus(`Erro no stream: ${(e && e.message) || e}`);
      }
    }
  }
}
