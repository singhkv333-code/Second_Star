function renderInline(text, keyBase) {
  const parts = [];
  const boldRe = /\*\*([^*]+)\*\*/g;
  let lastIdx = 0;
  let m;
  let i = 0;
  while ((m = boldRe.exec(text)) !== null) {
    if (m.index > lastIdx) parts.push(renderRupees(text.slice(lastIdx, m.index), `${keyBase}-t-${i++}`));
    parts.push(
      <strong key={`${keyBase}-b-${i++}`} style={{ color: '#fff', fontWeight: 600 }}>
        {renderRupees(m[1], `${keyBase}-bs-${i++}`)}
      </strong>
    );
    lastIdx = boldRe.lastIndex;
  }
  if (lastIdx < text.length) parts.push(renderRupees(text.slice(lastIdx), `${keyBase}-t-${i++}`));
  return parts;
}

function renderRupees(text, keyBase) {
  const re = /(₹[\d,]+(?:\.\d+)?)/g;
  const out = [];
  let lastIdx = 0;
  let m;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > lastIdx) out.push(text.slice(lastIdx, m.index));
    out.push(
      <span key={`${keyBase}-r-${i++}`} style={{ fontFamily: 'var(--font-mono)', color: '#fff' }}>
        {m[1]}
      </span>
    );
    lastIdx = re.lastIndex;
  }
  if (lastIdx < text.length) out.push(text.slice(lastIdx));
  return out.length === 1 ? out[0] : out;
}

function renderMarkdown(text) {
  if (!text) return null;
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, '').trim();
  text = text.replace(/^#{1,6}\s+/gm, '');
  const lines = text.split('\n').filter((l) => l.trim() !== '');
  return lines.map((line, i) => (
    <p key={i} style={{ margin: i === 0 ? 0 : '8px 0 0', lineHeight: 1.65 }}>
      {renderInline(line, `l${i}`)}
    </p>
  ));
}

export function MessageBubble({ message }) {
  const isUser = message.role === 'user';

  return (
    <div style={{
      display: 'flex',
      justifyContent: isUser ? 'flex-end' : 'flex-start',
      marginBottom: 16,
    }}>
      {!isUser && (
        <div style={{
          width: 24, height: 24, borderRadius: '50%',
          background: 'rgba(255,255,255,0.06)',
          border: '1px solid rgba(255,255,255,0.1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 10, color: 'rgba(255,255,255,0.4)',
          marginRight: 10, marginTop: 4, flexShrink: 0,
          fontFamily: 'var(--font-display)', fontStyle: 'italic',
        }}>P</div>
      )}

      <div style={{
        maxWidth: '72%',
        padding: '12px 16px',
        borderRadius: isUser ? '16px 16px 4px 16px' : '4px 16px 16px 16px',
        background: isUser ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)',
        border: '1px solid',
        borderColor: isUser ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.07)',
        boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.04)',
        backdropFilter: 'blur(8px)',
        WebkitBackdropFilter: 'blur(8px)',
        fontSize: 14,
        color: isUser ? '#fff' : 'rgba(255,255,255,0.9)',
        lineHeight: 1.65,
        fontFamily: 'var(--font-ui)',
      }}>
        {isUser ? <span>{message.content}</span> : renderMarkdown(message.content)}

        {message.logicCard && (
          <div style={{
            marginTop: 12,
            padding: '9px 12px',
            background: 'rgba(34,197,94,0.05)',
            border: '1px solid rgba(34,197,94,0.15)',
            borderRadius: 8,
            fontSize: 12,
            color: 'rgba(34,197,94,0.85)',
            display: 'flex', alignItems: 'center', gap: 8,
          }}>
            <span style={{ opacity: 0.7 }}>◈</span>
            Strategy ready — confirm in Orders panel
          </div>
        )}

        {message.timestamp && (
          <div style={{
            marginTop: 6, fontSize: 10,
            color: 'rgba(255,255,255,0.2)',
            textAlign: isUser ? 'right' : 'left',
          }}>
            {new Date(message.timestamp).toLocaleTimeString('en-IN', {
              hour: '2-digit', minute: '2-digit',
            })}
          </div>
        )}
      </div>
    </div>
  );
}
