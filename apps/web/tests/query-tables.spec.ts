import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function openChat(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect(page.getByRole('heading', { name: '让重要的来信，先被看见。' })).toBeVisible();
  await page.goto('/chat');
}

async function tableFixture(page: Page, legacy = false) {
  const subjects = [
    '新设备登录提醒',
    '入会申请材料已成功提交',
    '账号安全通知',
    '登录安全提示',
    '邮箱开通欢迎邮件',
    '邮箱激活成功通知',
  ];
  const sources = subjects.map((subject, index) => ({
    id: `table-mail-${index}`,
    subject,
    sender: '邮箱服务',
    account_label: '学习邮箱',
    sent_at: '2026-11-04T06:26:00+00:00',
    received_at: index === 5 ? null : '2026-11-04T06:26:00+00:00',
    body_truncated: index === 4,
    folder: 'INBOX',
    observed_state: 'current',
    last_observed_at: null,
  }));
  const assistant = {
    id: 'table-answer',
    conversation_id: 'table-conversation',
    role: 'assistant',
    task_id: null,
    created_at: '2026-11-05T03:00:00+00:00',
    content: '原有文字回答及邮件依据。',
    sources,
    presentation: legacy
      ? null
      : {
          version: 1,
          type: 'mail_table',
          summary: '有 1 项需要确认，1 项等待反馈，其余通知按需查看。',
          rows: sources.map((source, index) => ({
            mail_id: source.id,
            handling: index === 0 ? 'confirm' : index === 1 ? 'waiting' : 'notice',
            action:
              index === 0
                ? '核对是否本人操作；本人操作可忽略。'
                : index === 1
                  ? '等待审核结果。'
                  : '按需阅读通知。',
          })),
          notes: ['部分邮件仅有正文片段，请核对原文。'],
        },
  };
  await page.route('**/api/v1/conversations', (route) =>
    route.fulfill({
      json: [
        {
          id: 'table-conversation',
          title: '事项表格验收',
          channel: 'web',
          created_at: assistant.created_at,
        },
      ],
    }),
  );
  await page.route('**/api/v1/conversations/table-conversation/messages', (route) =>
    route.fulfill({ json: [assistant] }),
  );
  await page.route('**/api/v1/mails/table-mail-**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const id = path.split('/')[4];
    const source = sources.find((item) => item.id === id)!;
    const mail = {
      ...source,
      account_id: 'demo_163',
      recipients: [],
      body: `${source.subject}正文样例`,
      priority: null,
      message_id: `<${id}@example.invalid>`,
      references: [],
    };
    return route.fulfill({
      json: path.endsWith('/thread')
        ? { linked: [mail], subject_candidates: [], warnings: [] }
        : mail,
    });
  });
  await openChat(page);
  await page.locator('.conversation-list button').filter({ hasText: '事项表格验收' }).click();
}

test('real overview task saves a table and retains it after reload', async ({ page }) => {
  await openChat(page);
  await page.getByRole('button', { name: '今天有什么需要我处理的邮件？', exact: true }).click();
  const table = page.getByRole('table', { name: '邮件事项与处理建议' });
  await expect(table).toBeVisible();
  await expect(table).toContainText('课程项目阶段材料提交');
  await expect(table).toContainText('其他通知 · 2 封');
  await expect(page.locator('.query-answer-source-count')).toContainText('6 封');
  await expect(page.locator('.chat-message.assistant .source-list')).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('table', { name: '邮件事项与处理建议' })).toBeVisible();
  await page.getByRole('button', { name: /展开其他通知 2 封/ }).click();
  await expect(table).toContainText('本周人工智能研究简报');
});

test('compact table expands notifications and opens the exact source', async ({ page }) => {
  await tableFixture(page);
  const table = page.getByRole('table', { name: '邮件事项与处理建议' });
  await expect(table.locator('tbody tr')).toHaveCount(3);
  await expect(page.getByText('邮箱开通欢迎邮件', { exact: true })).toHaveCount(0);
  await expect(page.getByText('原有文字回答及邮件依据。', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '展开其他通知 4 封' }).click();
  await expect(table.locator('tbody tr')).toHaveCount(7);
  await expect(page.getByText('收件时间未记录', { exact: false })).toBeVisible();
  await expect(page.locator('.query-fragment')).toContainText('正文片段');
  await page.getByRole('button', { name: '查看邮箱开通欢迎邮件原文', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('邮箱开通欢迎邮件正文样例');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '收起其他通知 4 封' }).click();
  await expect(table.locator('tbody tr')).toHaveCount(3);
  await page.screenshot({ path: 'test-results/query-table-desktop.png', fullPage: true });
  const report = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  expect(report.violations).toEqual([]);
});

test('mobile table preserves actions, notices and source access without overflow', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await tableFixture(page);
  await expect(page.locator('.query-answer-summary')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: 'test-results/query-table-mobile.png', fullPage: true });
  await page.getByRole('button', { name: '展开其他通知 4 封' }).click();
  await page.getByRole('button', { name: '查看邮箱激活成功通知原文', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('邮箱激活成功通知正文样例');
});

test('legacy answers continue to render their original content and source buttons', async ({
  page,
}) => {
  await tableFixture(page, true);
  await expect(page.getByText('原有文字回答及邮件依据。', { exact: true })).toBeVisible();
  await expect(page.locator('.query-answer-table')).toHaveCount(0);
  await expect(page.locator('.source-list button')).toHaveCount(6);
  await page.locator('.source-list button').filter({ hasText: '新设备登录提醒' }).click();
  await expect(page.getByRole('dialog')).toContainText('新设备登录提醒正文样例');
});
