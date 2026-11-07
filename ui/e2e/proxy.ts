import http from "node:http";
import net from "node:net";
import type { AddressInfo } from "node:net";

/**
 * Stands in for the nginx on pisces-01, from its real config: the prefix is
 * stripped, Host is $host (no port), and X-Real-IP and X-Forwarded-For are set.
 * WebSocket upgrades pass only if the location sets Upgrade and Connection.
 */
export async function startProxy(targetPort: number, { prefix = "/agentop", websockets = true } = {}) {
  let upgrades = websockets;
  // closeAllConnections doesn't cover upgraded sockets, and close() waits for them.
  const tunnels = new Set<net.Socket>();
  const strip = (url = "") => (url.startsWith(`${prefix}/`) ? url.slice(prefix.length) : null);
  const forwarded = (req: http.IncomingMessage) => ({
    host: (req.headers.host ?? "").replace(/:\d+$/, ""),
    "x-forwarded-for": "203.0.113.9",
    "x-real-ip": "203.0.113.9",
  });

  const server = http.createServer((req, res) => {
    const path = strip(req.url);
    if (path === null) {
      res.writeHead(404).end();
      return;
    }
    const upstream = http.request(
      { port: targetPort, path, method: req.method, headers: { ...req.headers, ...forwarded(req) } },
      (reply) => {
        res.writeHead(reply.statusCode!, reply.headers);
        reply.pipe(res);
      },
    );
    upstream.on("error", () => res.destroy());
    req.pipe(upstream);
  });

  server.on("upgrade", (req, socket, head) => {
    const path = strip(req.url);
    if (path === null || !upgrades) {
      // Without the headers nginx forwards a plain GET, which the service has no route for.
      socket.end("HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
      return;
    }
    const client = socket as net.Socket;
    tunnels.add(client);
    client.once("close", () => tunnels.delete(client));
    const upstream = net.connect(targetPort, "127.0.0.1", () => {
      const headers = { ...req.headers, ...forwarded(req) };
      const lines = Object.entries(headers).map(([k, v]) => `${k}: ${v}`);
      upstream.write(`GET ${path} HTTP/1.1\r\n${lines.join("\r\n")}\r\n\r\n`);
      upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    });
    upstream.on("error", () => socket.destroy());
    socket.on("error", () => upstream.destroy());
  });

  await new Promise<void>((resolve) => server.listen(0, "0.0.0.0", resolve));
  return {
    port: (server.address() as AddressInfo).port,
    setWebsockets: (on: boolean) => { upgrades = on; },
    close: () =>
      new Promise((resolve) => {
        server.close(resolve);
        server.closeAllConnections();
        tunnels.forEach((s) => s.destroy());
      }),
  };
}
