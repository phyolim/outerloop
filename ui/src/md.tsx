/* Tiny markdown renderer for ticket bodies and thread comments: headings, bold,
   italic, inline code, fenced code blocks, links, images, lists. Escape-first so
   raw HTML in user/agent text never survives — dependency-free and XSS-safe by
   construction. ponytail: a md subset, swap in a real parser if tables/nesting
   ever matter. */

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/* Only link/image targets that can't run script. Text is already escaped. */
function safeUrl(u: string): string {
  return /^(https?:\/\/|\/|#|mailto:)/i.test(u) ? u : '#'
}

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) => `<img src="${safeUrl(url)}" alt="${alt}" loading="lazy" />`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, txt, url) => `<a href="${safeUrl(url)}" target="_blank" rel="noopener">${txt}</a>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\s)\*([^*\n]+)\*(?=\s|$)/g, '$1<em>$2</em>')
}

export function renderMarkdown(src: string): string {
  const out: string[] = []
  // Fences split the doc into alternating text / code segments.
  const parts = esc(src).split(/^```[^\n]*\n?/m)
  parts.forEach((part, pi) => {
    if (pi % 2 === 1) {
      out.push(`<pre><code>${part.replace(/\n$/, '')}</code></pre>`)
      return
    }
    let list: 'ul' | 'ol' | null = null
    const closeList = () => {
      if (list) out.push(`</${list}>`)
      list = null
    }
    for (const line of part.split('\n')) {
      const h = line.match(/^(#{1,4})\s+(.*)/)
      const li = line.match(/^\s*([-*]|\d+\.)\s+(.*)/)
      if (h) {
        closeList()
        out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`) // h1 -> h3 etc: thread-scale
      } else if (li) {
        const want = /\d/.test(li[1]) ? 'ol' : 'ul'
        if (list !== want) {
          closeList()
          out.push(`<${want}>`)
          list = want
        }
        out.push(`<li>${inline(li[2])}</li>`)
      } else if (line.trim() === '') {
        closeList()
      } else {
        closeList()
        out.push(`<p>${inline(line)}</p>`)
      }
    }
    closeList()
  })
  return out.join('')
}

export function Md({ source, className = '' }: { source: string; className?: string }) {
  return (
    <div
      className={`md text-[13px] leading-[1.6] text-[#c6ccd8] ${className}`}
      dangerouslySetInnerHTML={{ __html: renderMarkdown(source) }}
    />
  )
}
