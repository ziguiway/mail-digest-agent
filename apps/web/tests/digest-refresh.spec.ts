import { expect, test, type Page } from '@playwright/test';

async function openDigests(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect
    .poll(async () => {
      const digests = await (await page.request.get('/api/v1/digests')).json();
      return (
        Array.isArray(digests) &&
        digests.some((digest: { items: unknown[] }) => digest.items.length === 5)
      );
    })
    .toBe(true);
  await page.goto('/digests');
  await expect(page.locator('.digest-item')).toHaveCount(5);
}

function terminalTask(
  status: 'failed' | 'succeeded',
  result: Record<string, unknown> | null,
  error: string | null = null,
) {
  return {
    id: 'fixture-refresh',
    kind: 'digest',
    lane: 'background',
    status,
    phase: status === 'failed' ? '执行失败' : '执行完成',
    payload: {},
    result,
    error,
    error_code: error ? 'model_output_error' : null,
    attempts: 1,
    max_attempts: 3,
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: new Date().toISOString(),
  };
}

test('repeated refresh retains all five mails without adding versions or sending', async ({
  page,
}) => {
  await openDigests(page);
  const before = await (await page.request.get('/api/v1/digests')).json();
  for (let round = 0; round < 2; round += 1) {
    await page.getByRole('button', { name: '刷新摘要', exact: true }).click();
    await expect(page.getByRole('button', { name: '刷新摘要', exact: true })).toBeEnabled();
    await expect(page.getByText('已是最新，内容未变化', { exact: true })).toBeVisible();
    await expect(page.locator('.digest-item')).toHaveCount(5);
  }
  const after = await (await page.request.get('/api/v1/digests')).json();
  expect(after.length).toBe(before.length);
  expect(after[0].items).toHaveLength(5);
  await expect(page.getByText('最后检查', { exact: false }).first()).toBeVisible();
  await page.screenshot({ path: 'test-results/digest-refresh-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.sidebar')).not.toBeInViewport();
  await expect(page.getByRole('button', { name: '刷新摘要', exact: true })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: 'test-results/digest-refresh-mobile.png', fullPage: true });
});

test('restored old empty task cannot select an empty legacy digest', async ({ page }) => {
  await page.addInitScript(() =>
    sessionStorage.setItem('mail:last-task:/digests', 'legacy-empty-task'),
  );
  await page.route('**/api/v1/tasks/legacy-empty-task', async (route) =>
    route.fulfill({
      json: {
        ...terminalTask('succeeded', { digest_id: 'legacy-empty' }),
        id: 'legacy-empty-task',
      },
    }),
  );
  await page.route('**/api/v1/digests', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch();
    const digests = await response.json();
    await route.fulfill({
      response,
      json: [
        {
          ...digests[0],
          id: 'legacy-empty',
          group_id: null,
          legacy: true,
          revision: 0,
          items: [],
          range_start: null,
          range_end: null,
          title: '旧版空记录',
        },
        {
          ...digests[0],
          id: 'legacy-five',
          group_id: null,
          legacy: true,
          title: '旧版五条摘要',
        },
        ...digests,
      ],
    });
  });
  await openDigests(page);
  await expect(page.locator('.legacy-digests summary')).toContainText('旧版摘要');
  await expect(page.getByRole('button', { name: /旧版空记录/ })).not.toBeVisible();
  await page.locator('.legacy-digests summary').click();
  await expect(page.getByRole('button', { name: /旧版空记录/ })).toBeVisible();
  await page.locator('.digest-account-picker summary').click();
  await page.getByRole('checkbox').first().uncheck();
  await expect(page.getByRole('heading', { name: '生成第一份最近 24 小时摘要' })).toBeVisible();
  await expect(page.locator('.digest-item')).toHaveCount(0);
});

test('failed refresh leaves the previous five items visible', async ({ page }) => {
  await openDigests(page);
  await page.route('**/api/v1/digests', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 202,
      json: terminalTask('failed', null, '模型服务暂时不可用，保留上次摘要'),
    });
  });
  await page.getByRole('button', { name: '刷新摘要', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('保留上次摘要');
  await expect(page.locator('.digest-item')).toHaveCount(5);
});

test('true empty range has a distinct state and creates no history entry', async ({ page }) => {
  await page.route('**/api/v1/digests', async (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: [] });
    return route.fulfill({
      status: 202,
      json: terminalTask('succeeded', {
        digest_id: null,
        group_id: 'empty-range',
        empty: true,
        reused: false,
        account_ids: ['demo_qq', 'demo_163'],
        range_end: new Date().toISOString(),
        message: '最近 24 小时没有邮件',
      }),
    });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.goto('/digests');
  await page.getByRole('button', { name: '刷新摘要', exact: true }).click();
  await expect(page.getByRole('heading', { name: '最近 24 小时没有邮件' })).toBeVisible();
  await expect(page.locator('.digest-history-group')).toHaveCount(0);
});

test('empty priority filter still links to the five-item full digest', async ({ page }) => {
  await page.route('**/api/v1/digests', async (route) => {
    const response = await route.fetch();
    const digests = await response.json();
    await route.fulfill({
      response,
      json: digests.map((d: { items: Record<string, unknown>[] }) => ({
        ...d,
        items: d.items.map((item) => ({ ...item, priority: 'low' })),
      })),
    });
  });
  await openDigests(page);
  await page.getByRole('tab', { name: /优先处理/ }).click();
  await expect(page.getByRole('heading', { name: '该分类暂无事项' })).toBeVisible();
  await page.getByRole('button', { name: '查看全部 5 条' }).click();
  await expect(page.locator('.digest-item')).toHaveCount(5);
});

test('same-day versions stay in one history group and old content remains accessible', async ({
  page,
}) => {
  await page.route('**/api/v1/digests', async (route) => {
    const response = await route.fetch();
    const digests = await response.json();
    const first = digests.find((d: { legacy?: boolean }) => !d.legacy);
    const second = {
      ...first,
      id: 'new-version',
      revision: 2,
      source_count: 7,
      items: [
        ...first.items,
        { ...first.items[0], mail_id: 'ui-new-6', summary: '新的课程通知' },
        { ...first.items[0], mail_id: 'ui-new-7', summary: '新的实验安排' },
      ],
      created_at: new Date().toISOString(),
    };
    await route.fulfill({ response, json: [second, ...digests] });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.goto('/digests');
  await expect(page.locator('.digest-item')).toHaveCount(7);
  await expect(page.locator('.digest-history-group')).toHaveCount(1);
  await page.getByText('历史版本（2）', { exact: true }).click();
  await page.getByRole('button', { name: /版本 1 · 5 件事项/ }).click();
  await expect(page.locator('.digest-item')).toHaveCount(5);
});
