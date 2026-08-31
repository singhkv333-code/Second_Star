select dimension, value from (
select 1 ord,'Waitlist registrations (1 month)' dimension, (select count(*)::text from demo_waitlist) value
union all select 2,'Cities represented', (select count(distinct city)::text from demo_waitlist)
union all select 3,'Activated users', (select count(*)::text from demo_waitlist where activated)
union all select 4,'Distinct securities rendered', (select count(distinct symbol)::text from demo_security_render)
union all select 5,'Securities data render events', (select count(*)::text from demo_security_render)
union all select 6,'Exchanges covered (NSE / BSE / SME)', (select count(distinct exchange)::text from demo_security_render)
union all select 7,'AI chat sessions', (select count(*)::text from demo_chat_session)
union all select 8,'AI conversation turns', (select sum(turns)::text from demo_chat_session)
union all select 9,'Chart tool calls from chat', (select sum(tools_used)::text from demo_chat_session)
) t order by ord;
