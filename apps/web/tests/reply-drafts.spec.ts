import { expect, test } from '@playwright/test';

test('generate, edit and copy a reply draft from a digest item', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect(page.getByRole('heading', { name: '让重要的来信，先被看见。' })).toBeVisible();
  await page.goto('/digests');
  const meeting = page.locator('.digest-item').filter({ hasText: '课题组会议时间更新' });
  await meeting.getByRole('button', { name: '起草回复' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: '起草回复' })).toBeVisible();
  await expect(dialog.getByRole('button', { name: '生成草稿' })).toBeDisabled();
  await dialog.getByLabel('回复要点').fill('确认参会，询问是否需要提前提交 PPT。');
  await dialog.getByRole('button', { name: '生成草稿' }).click();
  await expect(dialog.getByLabel('邮件主题')).toHaveValue('Re: 课题组会议时间更新');
  await expect(dialog.getByLabel('草稿正文')).toHaveValue(/16:00.*PPT/s);
  await expect(dialog.getByText('本次参考了原邮件和 1 封引用关联的往来。')).toBeVisible();
  await page.screenshot({ path: 'test-results/reply-draft.png' });

  const edited = '老师您好，我可以参加周五 16:00 的组会。';
  await dialog.getByLabel('草稿正文').fill(edited);
  await dialog.getByRole('button', { name: '查看原邮件' }).click();
  await expect(dialog.getByRole('heading', { name: '课题组会议时间更新' })).toBeVisible();
  await expect(dialog.getByText(/地点 B202/)).toBeVisible();
  await dialog.getByRole('button', { name: '起草回复' }).click();
  await expect(dialog.getByLabel('草稿正文')).toHaveValue(edited);
  await dialog.getByRole('button', { name: '复制草稿' }).click();
  await expect(dialog.getByText('已复制主题和正文')).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    `主题：Re: 课题组会议时间更新\n\n${edited}`,
  );
  await page.keyboard.press('Escape');
  await page.goto('/mail');
  await page.locator('.mail-row').filter({ hasText: '课题组会议时间更新' }).click();
  await dialog.getByRole('button', { name: '起草回复' }).click();
  await expect(dialog.getByLabel('回复要点')).toBeVisible();
});
