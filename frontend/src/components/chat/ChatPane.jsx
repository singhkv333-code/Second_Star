import { useState, useRef, useEffect } from 'react';
import { sendChat } from '../../api/endpoints';
import { MessageBubble } from './MessageBubble';

function parseLogicCard(text) {
  const match = text.match(/<LOGICCARD>([\s\S]*?)<\/LOGICCARD>/);
  if (!match) return { text, logicCard: null };
  try {
    return {
      text: text.replace(/<LOGICCARD>[\s\S]*?<\/LOGICCARD>/, '').trim(),
      logicCard: JSON.parse(match[1]),
    };
  } catch {
    return { text, logicCard: null };
  }
}

export function ChatPane({ onOrderPreview }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: "Welcome to Pivot. I can execute orders, build investment products, and analyse your portfolio. What would you like to do?" }
  ]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  const sendMessage = async () => {
    if (!input.trim() || isTyping) return;
    const userMsg = { role: 'user', content: input.trim(), timestamp: new Date().toISOString() };
    setMessages((m) => [...m, userMsg]);
    setInput('');
    setIsTyping(true);

    try {
      const allMessages = [...messages, userMsg];
      // Sarvam (and OpenAI-style APIs) require the conversation to start with a user
      // turn — drop any leading assistant messages (like the seed greeting).
      const trimmed = allMessages.slice(-12);
      const firstUserIdx = trimmed.findIndex((m) => m.role === 'user');
      const payload = firstUserIdx === -1 ? trimmed : trimmed.slice(firstUserIdx);
      const res = await sendChat(payload.map(({ role, content }) => ({ role, content })));
      const raw = res.data.response || '';
      const { text, logicCard } = parseLogicCard(raw);
      const aiMsg = {
        role: 'assistant',
        content: text,
        logicCard: logicCard || null,
        timestamp: new Date().toISOString(),
      };
      setMessages((m) => [...m, aiMsg]);
      if (logicCard) {
        onOrderPreview({ ...logicCard, isAIGenerated: true });
      }
    } catch (err) {
      setMessages((m) => [...m, {
        role: 'assistant',
        content: 'Something went wrong on our end. Please try again in a moment.',
        timestamp: new Date().toISOString(),
      }]);
    } finally {
      setIsTyping(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ flex: 1, overflowY: 'auto', padding: '20px' }}>
        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}

        {isTyping && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 16 }}>
            <div style={{
              padding: '12px 18px',
              background: 'rgba(255,255,255,0.04)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '16px 16px 16px 4px',
              display: 'flex', gap: 4, alignItems: 'center',
            }}>
              {[0,1,2].map((i) => (
                <span key={i} style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: 'rgba(255,255,255,0.4)',
                  animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
                  display: 'inline-block',
                }} />
              ))}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div style={{
        padding: '16px 20px',
        borderTop: '1px solid rgba(255,255,255,0.06)',
        display: 'flex', gap: 10,
      }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && sendMessage()}
          placeholder="Ask anything — 'buy 10 INFY at market', 'show my portfolio', 'explain SafeGrow'..."
          style={{
            flex: 1, padding: '12px 16px',
            background: 'rgba(255,255,255,0.04)',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: 12, color: '#fff', fontSize: 14,
            fontFamily: 'var(--font-ui)', outline: 'none',
            transition: 'border-color 150ms',
          }}
          onFocus={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.2)'}
          onBlur={(e) => e.target.style.borderColor = 'rgba(255,255,255,0.08)'}
        />
        <button onClick={sendMessage} disabled={!input.trim() || isTyping} style={{
          padding: '12px 20px',
          background: input.trim() && !isTyping ? 'rgba(255,255,255,0.1)' : 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: 12, color: '#fff', cursor: input.trim() && !isTyping ? 'pointer' : 'default',
          fontSize: 18, transition: 'all 150ms',
        }}>↑</button>
      </div>
    </div>
  );
}
