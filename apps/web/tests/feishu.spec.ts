import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

function task(id = 'fixture-task') {
  return {
    id,
    kind: 'feishu_authorize',
    lane: 'control',
    status: 'succeeded',
    phase: '执行完成',
    payload: {},
    result: {},
    error: null,
    error_code: null,
    attempts: 1,
    max_attempts: 1,
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
  };
}

test('binding UI shows opaque URL and QR, pairs the chat, previews the bot test, and enables delivery', async ({
  page,
}) => {
  // UI contract fixtures; subprocess/event integration is covered by pytest.
  let state: Record<string, unknown> = {
    binding_id: '',
    revision: 0,
    status: 'unconfigured',
    configured: false,
    enabled: false,
    urls: [],
    listener: { ready: false },
    test_message: '来信连接测试：已成功绑定此会话。',
    simulated: false,
  };
  const url = 'https://open.feishu.cn/fake/verify?code=test%2Bopaque&next=x%252Fy';
  let testedBinding = '';
  await page.route('**/api/v1/settings/feishu**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.includes('/qr/'))
      return route.fulfill({
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPXsAAAAASUVORK5CYII=',
          'base64',
        ),
      });
    if (path.endsWith('/bind')) {
      state = {
        ...state,
        binding_id: 'fixture-app',
        status: 'authorizing',
        profile: 'mail-digest-fixture',
        urls: [{ url, qr_id: 'a'.repeat(64) }],
      };
      return route.fulfill({ status: 202, json: task() });
    }
    if (path.endsWith('/pair'))
      state = {
        ...state,
        binding_id: 'fixture-recipient',
        status: 'pairing',
        pairing_code: 'MAIL-0123456789',
        pairing_expires_at: new Date(Date.now() + 600000).toISOString(),
      };
    if (path.endsWith('/test')) {
      testedBinding = route.request().postDataJSON().binding_id;
      state = { ...state, last_test_at: new Date().toISOString() };
      return route.fulfill({ status: 202, json: task('fixture-test-send') });
    }
    if (method === 'PUT') state = { ...state, ...route.request().postDataJSON() };
    return route.fulfill({ json: state });
  });
  await page.route('**/api/v1/tasks/fixture*', (route) => route.fulfill({ json: task() }));
  await page.route('**/api/v1/deliveries', (route) => route.fulfill({ json: [] }));
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '连接与设置', exact: true }).click();
  await page.getByRole('button', { name: '飞书连接', exact: true }).click();
  await page.getByRole('button', { name: '创建专用应用并开始绑定' }).click();
  await expect(page.locator('.raw-auth-link')).toHaveAttribute('href', url);
  await expect(page.getByAltText('飞书授权或配置二维码')).toBeVisible();
  state = {
    ...state,
    status: 'ready',
    configured: true,
    app_id: 'cli_course_fixture',
    urls: [],
    listener: { ready: true },
  };
  await expect(page.getByRole('button', { name: '生成配对码' })).toBeEnabled();
  await page.getByRole('button', { name: '生成配对码' }).click();
  await expect(page.locator('.pairing-box')).toContainText('MAIL-0123456789');
  state = {
    ...state,
    status: 'paired',
    pairing_code: null,
    owner_open_id: 'ou_course_fixture',
    chat_id: 'oc_course_fixture',
    paired_at: new Date().toISOString(),
  };
  await expect(page.locator('.paired-target')).toContainText('oc_course_fixture');
  await expect(page.locator('.test-message')).toContainText('来信连接测试');
  await page.getByRole('button', { name: '以机器人身份发送测试消息' }).click();
  expect(testedBinding).toBe('fixture-recipient');
  await expect(page.getByLabel('启用飞书收发与摘要推送')).toBeEnabled();
  await page.getByLabel('启用飞书收发与摘要推送').click();
  await expect(page.getByText('现在可以在飞书里使用')).toBeVisible();
  expect(
    (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations,
  ).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/feishu-binding-desktop.png', fullPage: true });
});

test('uncertain delivery requires the explicit retry choice', async ({ page }) => {
  let allowed = false;
  let delivery = {
    id: 'fixture-delivery',
    binding_id: 'fixture-binding',
    kind: 'test',
    source_id: 'test-source',
    recipient_chat_id: 'oc_course_fixture',
    status: 'uncertain',
    error: '上一次请求可能已送达，请核对飞书后显式重试',
    chunks: [{ index: 0, status: 'uncertain' }],
    task_status: 'failed',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  await page.route('**/api/v1/deliveries', (route) => route.fulfill({ json: [delivery] }));
  await page.route('**/api/v1/deliveries/fixture-delivery/retry', (route) => {
    allowed = route.request().postDataJSON().allow_uncertain;
    delivery = {
      ...delivery,
      status: 'sent',
      error: '',
      task_status: 'succeeded',
      chunks: [{ index: 0, status: 'sent' }],
    };
    return route.fulfill({ status: 202, json: task('fixture-retry') });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('link', { name: '任务记录', exact: true })
    .click();
  const retry = page.getByRole('button', { name: '重试未确认成功的分段' });
  await expect(retry).toBeDisabled();
  await page.getByRole('checkbox', { name: '我已核对飞书', exact: false }).check();
  await retry.click();
  expect(allowed).toBe(true);
  await expect(page.locator('.delivery-row')).toContainText('已推送');
});

test('sample mode explains the optional connection and cannot create a real app', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '连接与设置', exact: true }).click();
  await page.getByRole('button', { name: '飞书连接', exact: true }).click();
  await expect(page.getByRole('button', { name: '创建专用应用并开始绑定' })).toBeDisabled();
  await expect(
    page.getByText('当前为样例模式，绑定和发送操作已关闭。', { exact: false }),
  ).toBeVisible();
  expect(
    (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations,
  ).toEqual([]);
});

test('repeated retries of the same delivery resume task monitoring each time', async ({ page }) => {
  let attempts = 0;
  let reads = 0;
  let row = {
    id: 'same-delivery',
    binding_id: 'fixture',
    kind: 'test',
    source_id: 'test',
    recipient_chat_id: 'oc_fixture',
    status: 'failed',
    error: '临时发送失败',
    chunks: [],
    task_status: 'failed',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
  await page.route('**/api/v1/deliveries', (route) => route.fulfill({ json: [row] }));
  await page.route('**/api/v1/deliveries/same-delivery/retry', (route) => {
    attempts++;
    return route.fulfill({
      status: 202,
      json: { ...task('same-task'), kind: 'feishu_send', lane: 'delivery', status: 'queued' },
    });
  });
  await page.route('**/api/v1/tasks/same-task**', (route) => {
    if (new URL(route.request().url()).pathname.endsWith('/events'))
      return route.fulfill({ contentType: 'text/event-stream', body: 'event: done\ndata: {}\n\n' });
    reads++;
    const status = attempts === 1 ? 'failed' : 'succeeded';
    row = {
      ...row,
      status: status === 'failed' ? 'failed' : 'sent',
      task_status: status,
      error: status === 'failed' ? '临时发送失败' : '',
    };
    return route.fulfill({
      json: {
        ...task('same-task'),
        kind: 'feishu_send',
        lane: 'delivery',
        status,
        error: status === 'failed' ? '临时发送失败' : null,
      },
    });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page
    .getByRole('navigation', { name: '主导航' })
    .getByRole('link', { name: '任务记录', exact: true })
    .click();
  const retry = page.getByRole('button', { name: '重试未确认成功的分段' });
  await retry.click();
  await expect.poll(() => reads).toBeGreaterThan(0);
  await expect(retry).toBeEnabled();
  const firstReads = reads;
  await retry.click();
  await expect.poll(() => reads).toBeGreaterThan(firstReads);
  await expect(page.locator('.delivery-row')).toContainText('已推送');
});
