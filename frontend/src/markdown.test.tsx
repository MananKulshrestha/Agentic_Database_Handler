import { expect, test } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';

test('Markdown rendering does not execute embedded HTML', () => {
  const markup = renderToStaticMarkup(<ReactMarkdown>{'<script>alert("bad")</script>\n\n# Safe heading'}</ReactMarkdown>);
  expect(markup).not.toContain('<script>');
  expect(markup).toContain('<h1>Safe heading</h1>');
});
