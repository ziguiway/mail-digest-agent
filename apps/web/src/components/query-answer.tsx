import { useState } from 'react';
import { ArrowRight, Info } from '@phosphor-icons/react';
import type { Message, Source } from '@/api/client';
import { time } from '@/lib/utils';

type Props = { message: Message; onReadMail: (id: string) => void };
const handlingLabels = {
  action: '需要处理',
  confirm: '待确认',
  waiting: '等待反馈',
  notice: '按需查看',
};
const handlingOrder = { action: 0, confirm: 1, waiting: 2, notice: 3 };

export function hasQueryTable(message: Message) {
  const value = message.presentation;
  const ids = new Set((message.sources as Source[]).map((source) => source.id));
  return !!(
    value?.version === 1 &&
    value.type === 'mail_table' &&
    value.rows.length &&
    value.rows.every((row) => ids.has(row.mail_id))
  );
}

export function QueryAnswer({ message, onReadMail }: Props) {
  const [expanded, setExpanded] = useState(false);
  const value = message.presentation!;
  const notes = value.notes || [];
  const sources = new Map((message.sources as Source[]).map((source) => [source.id, source]));
  const rows = value.rows
    .slice()
    .sort((a, b) => handlingOrder[a.handling] - handlingOrder[b.handling]);
  const main = rows.filter((row) => row.handling !== 'notice');
  const notices = rows.filter((row) => row.handling === 'notice');
  const grouped = notices.length >= 2;
  const renderRow = (row: (typeof rows)[number]) => {
    const source = sources.get(row.mail_id)!;
    return (
      <tr key={row.mail_id}>
        <td data-label="处理建议">
          <span className={`handling-badge ${row.handling}`}>{handlingLabels[row.handling]}</span>
        </td>
        <td data-label="邮件事项" className="query-subject-cell">
          <strong>{source.subject}</strong>
          <span className="query-source-meta">
            {source.account_label} ·{' '}
            {source.received_at ? time(source.received_at) : '收件时间未记录'}
          </span>
          {source.body_truncated && <span className="query-fragment">正文片段</span>}
        </td>
        <td data-label="下一步">{row.action}</td>
        <td data-label="邮件依据">
          <button
            type="button"
            className="query-source-button"
            onClick={() => onReadMail(row.mail_id)}
            aria-label={`查看${source.subject}原文`}
          >
            查看原文 <ArrowRight size={13} />
          </button>
        </td>
      </tr>
    );
  };
  return (
    <div className="query-answer">
      <p className="query-answer-summary">{value.summary}</p>
      <div className="query-table-wrap">
        <table className="query-answer-table">
          <caption className="sr-only">邮件事项与处理建议</caption>
          <thead>
            <tr>
              <th scope="col" className="query-state-column">
                处理建议
              </th>
              <th scope="col" className="query-subject-column">
                邮件事项
              </th>
              <th scope="col">建议下一步</th>
              <th scope="col" className="query-evidence-column">
                邮件依据
              </th>
            </tr>
          </thead>
          <tbody>
            {main.map(renderRow)}
            {grouped ? (
              <>
                <tr className="query-notice-group">
                  <td data-label="处理建议">
                    <span className="handling-badge notice">按需查看</span>
                  </td>
                  <td data-label="邮件事项" className="query-subject-cell">
                    <strong>其他通知 · {notices.length} 封</strong>
                    <span className="query-source-meta">可展开查看每封邮件及其依据</span>
                  </td>
                  <td data-label="下一步">展开查看各封邮件的建议。</td>
                  <td data-label="邮件依据">
                    <button
                      className="query-source-button"
                      type="button"
                      aria-expanded={expanded}
                      aria-label={`${expanded ? '收起' : '展开'}其他通知 ${notices.length} 封`}
                      onClick={() => setExpanded(!expanded)}
                    >
                      {expanded ? '收起' : '展开'} {notices.length} 封
                    </button>
                  </td>
                </tr>
                {expanded && notices.map(renderRow)}
              </>
            ) : (
              notices.map(renderRow)
            )}
          </tbody>
        </table>
      </div>
      {!!notes.length && (
        <div className="query-answer-notes">
          <Info size={15} aria-hidden="true" />
          <div>
            {notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
          </div>
        </div>
      )}
      <p className="query-answer-source-count">邮件依据 · {sources.size} 封</p>
    </div>
  );
}
