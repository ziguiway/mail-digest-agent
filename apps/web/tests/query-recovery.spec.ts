import { expect, test, type Page } from '@playwright/test';

const taskId = 'query-recovery-fixture';
const conversationId = 'query-recovery-conversation';
const oldError = '模型查询未完成，请检查模型服务或缩小问题范围';
const answer = '查询已完成，可以查看邮件依据。';

function task(status: string, error: string | null = null) {
  return {
    id: taskId,
    kind: 'query',
    lane: 'interactive',
    status,
    payload: { conversation_id: conversationId },
    phase:
      status === 'running' ? '正在阅读邮件正文' : status === 'failed' ? '执行失败' : '执行完成',
    result:
      status === 'succeeded'
        ? { conversation_id: conversationId, message_id: 'fixture-answer' }
        : null,
    error,
    error_code: error ? 'model_timeout' : null,
    attempts: 2,
    max_attempts: 3,
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
  };
}

async function openChat(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect(page.getByRole('heading', { name: '让重要的来信，先被看见。' })).toBeVisible();
  await page.goto('/chat');
}

async function fixture(
  page: Page,
  current: () => ReturnType<typeof task>,
  unavailable: () => boolean = () => false,
) {
  await page.addInitScript((id) => sessionStorage.setItem('mail:last-task:query', id), taskId);
  await page.route(`**/api/v1/tasks/${taskId}/events`, (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: 'event: stage\ndata: {"phase":"正在阅读邮件正文"}\n\n',
    }),
  );
  await page.route(`**/api/v1/tasks/${taskId}`, (route) =>
    unavailable()
      ? route.fulfill({ status: 503, json: { detail: '暂时无法读取任务状态' } })
      : route.fulfill({ json: current() }),
  );
  await page.route(`**/api/v1/conversations/${conversationId}/messages`, (route) =>
    route.fulfill({
      json: [
        {
          id: 'fixture-question',
          conversation_id: conversationId,
          role: 'user',
          content: '今天有什么需要我处理的邮件？',
          sources: [],
          created_at: new Date().toISOString(),
        },
        ...(current().status === 'succeeded'
          ? [
              {
                id: 'fixture-answer',
                conversation_id: conversationId,
                role: 'assistant',
                content: answer,
                sources: [],
                created_at: new Date().toISOString(),
              },
            ]
          : []),
      ],
    }),
  );
}

test('restored automatic retry hides its old failure and clears it on success', async ({
  page,
}) => {
  let current = task('running', oldError);
  await fixture(page, () => current);
  await openChat(page);
  await expect(page.locator('.thinking')).toContainText('正在阅读邮件正文');
  await expect(page.getByRole('alert')).toHaveCount(0);
  current = task('succeeded');
  await expect(page.locator('.chat-message.assistant')).toContainText(answer);
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.locator('.thinking')).toHaveCount(0);
});

test('a recovered status connection clears its temporary error', async ({ page }) => {
  let current = task('running');
  let unavailable = false;
  await fixture(
    page,
    () => current,
    () => unavailable,
  );
  await openChat(page);
  await expect(page.locator('.thinking')).toBeVisible();
  unavailable = true;
  await expect(page.getByRole('alert')).toContainText('暂时无法读取任务状态');
  current = task('succeeded');
  unavailable = false;
  await expect(page.locator('.chat-message.assistant')).toContainText(answer);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('a final query failure remains visible and stops the progress indicator', async ({ page }) => {
  await fixture(page, () => task('failed', '模型服务响应超时，请稍后重试'));
  await openChat(page);
  await expect(page.getByRole('alert')).toContainText('模型服务响应超时');
  await expect(page.locator('.thinking')).toHaveCount(0);
});
