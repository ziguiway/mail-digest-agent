import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const baseURL = new URL(process.argv[2] || 'http://127.0.0.1:8774');
if (baseURL.protocol !== 'http:' || baseURL.hostname !== '127.0.0.1') {
  throw new Error('Capture requires an isolated loopback demo server.');
}

const output = new URL('../public/textures/', import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const layout = {};
const page = await browser.newPage({
  baseURL: baseURL.origin,
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 2,
  locale: 'zh-CN',
  reducedMotion: 'reduce',
});
page.setDefaultTimeout(15_000);

async function capture(name, selections) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    window.scrollTo(0, 0);
  });
  const pageH = await page.evaluate(() => document.documentElement.scrollHeight);
  const regions = {};
  for (const [key, locator] of Object.entries(selections)) {
    await locator.waitFor({ state: 'visible' });
    regions[key] = await locator.evaluate((element) => {
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x: x + window.scrollX, y: y + window.scrollY, w: width, h: height };
    });
  }
  const path = (suffix) => fileURLToPath(new URL(name + suffix + '.png', output));
  await page.screenshot({ path: path(''), fullPage: true, animations: 'disabled' });
  for (const [key, locator] of Object.entries(selections)) {
    await locator.screenshot({ path: path('-' + key), animations: 'disabled' });
  }
  layout[name] = { pageH, regions };
}

try {
  const auth = await page.request.get('/api/v1/auth/status');
  if (!auth.ok() || (await auth.json()).mode !== 'demo') {
    throw new Error('The server must run with the built-in demo data.');
  }
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '邮件', exact: true }).click();
  const rows = page.locator('.mail-row');
  await rows.nth(5).waitFor();
  await capture('mail', { first: rows.nth(0), second: rows.nth(1), third: rows.nth(2) });

  await page.getByRole('link', { name: '来信摘要', exact: true }).click();
  const digest = page.locator('.digest-item').filter({ hasText: '课程项目阶段材料提交' });
  await capture('digest', { action: digest, priorities: page.locator('.digest-item').nth(1) });
  await digest.getByRole('button', { name: '查看邮件依据' }).click();
  const dialog = page.getByRole('dialog');
  await page.locator('.mail-body').filter({ hasText: '上传项目说明到课程平台' }).waitFor();
  await capture('source', { original: dialog });
  await page.keyboard.press('Escape');

  await page.getByRole('link', { name: '智能查询', exact: true }).click();
  const compose = page.getByLabel('邮件问题');
  const ask = async (question, answerIndex) => {
    await compose.fill(question);
    await page.getByRole('button', { name: '发送问题' }).click();
    await page.locator('.chat-message.assistant').nth(answerIndex).waitFor();
  };
  await ask('找一下导师的会议通知。', 0);
  await ask('后来有更新吗？最终时间和地点是什么？', 1);
  await page.locator('.chat-message.assistant').last().getByText(/16:00/).first().waitFor();
  await page.locator('.chat-message.user').last().evaluate((element) => {
    element.scrollIntoView({ block: 'start' });
  });
  await capture('query', {
    question: page.locator('.chat-message.user').last(),
    answer: page.locator('.chat-message.assistant').last(),
  });

  await page.goto('/connections?tab=schedule');
  await page.getByRole('heading', { name: '汇总与同步计划' }).waitFor();
  await page.getByLabel('每日汇总时间').fill('08:30');
  await capture('schedule', { plan: page.locator('.settings-card').first() });
  await writeFile(new URL('../src/capture-layout.json', import.meta.url), JSON.stringify(layout, null, 2) + '\n');
} finally {
  await browser.close();
}
