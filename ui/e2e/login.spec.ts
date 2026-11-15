import { expect, test } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { Service } from "./service";

const PYTHON = `${process.env.HOME}/.pyenv/versions/3.13.9/bin/python`;

let s: Service;
let smtp: ChildProcess | null = null;
let mails: string;

/** A service with the Email code plugin on, sending to a fake mail server. */
async function withEmailCode(enabled = true) {
  s = new Service("alpha");
  const port = s.port + 1000;
  mails = join(s.root, "mails.jsonl");
  smtp = spawn(PYTHON, [resolve("../tests/fake_smtp.py"), String(port), mails], { stdio: ["ignore", "pipe", "ignore"] });
  await new Promise((ready) => smtp!.stdout!.once("data", ready));
  const home = join(s.root, "state");
  mkdirSync(join(home, "plugins", "email-code"), { recursive: true });
  mkdirSync(join(home, "state"), { recursive: true });
  writeFileSync(join(home, "state", "plugins.json"), JSON.stringify({ "email-code": { enabled } }));
  writeFileSync(
    join(home, "plugins", "email-code", "config.toml"),
    `to = "me@example.com"\nsmtp_host = "127.0.0.1"\nsmtp_port = ${port}\nsmtp_security = "none"\n`,
  );
  await s.start();
}

function lastCode(): string {
  const lines = existsSync(mails) ? readFileSync(mails, "utf8").trim().split("\n") : [];
  const last = JSON.parse(lines[lines.length - 1]);
  return /\b(\d{6})\b/.exec(last.subject)![1];
}

test.afterEach(async () => {
  smtp?.kill();
  smtp = null;
  await s.dispose();
});

test("a code sent by email signs the app in, and survives a reload while you read it", async ({ page }) => {
  await withEmailCode();
  await page.goto(s.url);
  await expect(page.getByRole("heading", { name: `Sign in to ${new URL(s.url).host}` })).toBeVisible();
  const method = page.getByRole("region", { name: "Email code" });
  await method.getByRole("button", { name: "Send me a sign-in code" }).click();
  await expect(method.getByText("Code sent. It works for 10 minutes.")).toBeVisible();
  // Nothing about where it went.
  await expect(page.getByText("me@example.com")).toHaveCount(0);

  await method.getByLabel("Code").fill("000000" === lastCode() ? "111111" : "000000");
  await method.getByRole("button", { name: "Sign in" }).click();
  await expect(method.getByRole("alert")).toHaveText("Wrong or expired code.");

  await page.reload();
  await expect(method.getByLabel("Code")).toBeVisible();
  await method.getByLabel("Code").fill(lastCode());
  await method.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: /Start a session/ })).toBeVisible();
  const [saved] = JSON.parse((await page.evaluate(() => localStorage.getItem("agentop.connections")))!);
  expect(saved.token).toBeTruthy();
  expect(saved.clientId).toBeTruthy();
});

test("with the plugin off, the sign-in screen has only the token form", async ({ page }) => {
  await withEmailCode(false);
  await page.goto(s.url);
  await expect(page.getByRole("heading", { name: `Enter the token for ${new URL(s.url).host}` })).toBeVisible();
  await expect(page.getByLabel("Token")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send me a sign-in code" })).toHaveCount(0);
});
