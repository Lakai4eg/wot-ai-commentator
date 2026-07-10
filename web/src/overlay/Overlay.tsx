import { useEffect, useRef, useState } from "react";

interface Replica {
  id: number;
  text: string;
  effect: string;
  serverId?: number;
}

/** Длительность показа плашки: базово 4с + чтение текста. */
function displayMs(text: string): number {
  return Math.min(4000 + text.length * 55, 12000);
}

// Плашка висит до 12с — без лимита очередь в бурный момент уезжает от игры
// на минуты. Лишнее дропаем с головы: свежая реплика важнее залежавшейся.
const MAX_QUEUE = 3;
const MAX_AUDIO_QUEUE = 3;

export function Overlay() {
  const [current, setCurrent] = useState<Replica | null>(null);
  const queueRef = useRef<Replica[]>([]);
  const busyRef = useRef(false);
  const idRef = useRef(0);

  // Очередь озвучки: реплики могут приходить чаще, чем звучит голос,
  // поэтому аудио проигрывается строго последовательно — следующий
  // url стартует только после окончания (или ошибки) предыдущего.
  const audioQueueRef = useRef<string[]>([]);
  const audioBusyRef = useRef(false);

  const playNextAudio = () => {
    const url = audioQueueRef.current.shift();
    if (!url) {
      audioBusyRef.current = false;
      return;
    }
    audioBusyRef.current = true;
    const audio = new Audio(url);
    const advance = () => playNextAudio();
    audio.addEventListener("ended", advance);
    audio.addEventListener("error", advance);
    audio.play().catch(advance);
  };

  const playNext = () => {
    const next = queueRef.current.shift();
    if (!next) {
      busyRef.current = false;
      return;
    }
    busyRef.current = true;
    setCurrent(next);
    setTimeout(() => {
      setCurrent(null);
      setTimeout(playNext, 500); // пауза между плашками
    }, displayMs(next.text));
  };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let pingTimer: number | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/overlay`);
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "audio") {
          // озвучка может догонять уже показанную реплику (audio_url
          // приходит позже своего текста) — ставим в очередь и играем
          // по порядку, не перекрывая предыдущий трек
          audioQueueRef.current.push(msg.audio_url);
          while (audioQueueRef.current.length > MAX_AUDIO_QUEUE) audioQueueRef.current.shift();
          if (!audioBusyRef.current) playNextAudio();
          return;
        }
        if (msg.type !== "replica") return;
        queueRef.current.push({ ...msg, serverId: msg.id, id: ++idRef.current });
        while (queueRef.current.length > MAX_QUEUE) queueRef.current.shift();
        if (!busyRef.current) playNext();
      };
      ws.onopen = () => {
        pingTimer = window.setInterval(() => ws?.send("ping"), 20000);
      };
      ws.onclose = () => {
        window.clearInterval(pingTimer);
        if (!closed) setTimeout(connect, 2000);
      };
    };
    connect();
    return () => {
      closed = true;
      window.clearInterval(pingTimer);
      ws?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="overlay-root">
      {current && current.text && (
        <div className={`plashka effect-${current.effect}`} key={current.id}>
          <span className="plashka-label">РЕЖИССЁР</span>
          <span className="plashka-text">{current.text}</span>
        </div>
      )}
    </div>
  );
}
