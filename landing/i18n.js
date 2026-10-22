/* ═══════════════════════════════════════════════════════════════
   ASTRA — i18n (EN / VI)
   Toggle via .lang-toggle button; persists in localStorage.
   Element markup: <tag data-i18n="key">…</tag>  (text-only)
                   <tag data-i18n-html="key">…</tag>  (HTML allowed)
   ═══════════════════════════════════════════════════════════════ */

const I18N = {
  en: {
    'dl.paused': 'Installer downloads are paused during development. You can still run Astra from source below.',
    // nav
    'nav.tag':          'v0.1 · personal build',
    'nav.showcase':     'App Showcase',
    'nav.usecases':     'Use cases',
    'nav.how':          'How it works',
    'nav.gov':          'Governance',
    'nav.compare':      'Why Astra',
    'nav.models':       'Models',
    'nav.integrations': 'Integrations',
    'nav.privacy':      'Privacy',
    'nav.approval':     'Approval flow',
    'nav.faq':          'FAQ',
    'nav.download':     'Download',
    'theme.dark':       'dark',
    'theme.light':      'light',
    'sb.status':        'live · session #a41f',
    'sb.label':         'Contents',
    'sb.legal':         '© 2026',

    // hero
    'hero.title':       'Your AI coworker that <em>actually</em><br>finishes the work.',
    'hero.sub':         'A desktop AI coworker. You give it real work — it hands back fixed code, finished docs, a triaged inbox. Not just chat.',
    'cta.run':          'Run from source',
    'cta.view':         'View source',
    'cta.note':         'Installer coming · You can run from source right now',
    'cta.download':     'Download Astra Desktop',
    'cta.platforms':    'macOS (Apple Silicon & Intel) · Windows 10/11 · Ubuntu/Debian · 100% Free & Open Source',
    'cta.star':         'Star on GitHub',
    'stat.connectors':  'connectors',
    'stat.providers':   'model providers',
    'stat.locked':      'cloud lock-in',
    'stat.gov':         'governance',

    // terminal tabs
    'term.tab.security': '01 Security Scan',
    'term.tab.cloud':    '02 Cloud Posture',
    'term.tab.incident': '03 Incident Triage',
    'term.tab.cowork':   '04 Daily Cowork',

    // desktop showcase
    'showcase.idx':     '01 / interactive showcase',
    'showcase.title':   'A true desktop coworker,<br><span class="grad-text">not a fragile browser tab</span>.',
    'showcase.sub':     'Watch Astra in motion on desktop: native workspace tree, transparent agent reasoning, human approval gate, and zero-compromise local governance.',
    'showcase.mode.video':  '▶ Video Walkthrough',
    'showcase.mode.sim':    '⌨ Interactive Workstation',
    'showcase.tab.chat':    'Tasks & Agent Chat',
    'showcase.tab.inbox':   'Approval Inbox',
    'showcase.tab.audit':   'Audit Log Trail',
    'showcase.badge':       '1 Pending',
    'showcase.agent.role':  'Astra Security Specialist · claude-3.5-sonnet',
    'showcase.agent.status':'Local Core Online · Sandboxed Session',
    'showcase.task.plan':   'Analyzing session-fixation risk in src/auth/session.ts...',
    'showcase.ask.title':   'Human Approval Required',
    'showcase.ask.desc':    'Astra prepared a security fix for session fixation. Applying this patch requires filesystem write authorization.',
    'showcase.ask.action':  'Patch write: src/auth/session.ts (+14 / -8 lines)',
    'showcase.btn.deny':    'Deny & Revoke',
    'showcase.btn.approve': 'Approve & Execute',
    'showcase.audit.title': 'Cryptographic Action Ledger',
    'showcase.audit.col1':  'Timestamp',
    'showcase.audit.col2':  'Action & Target',
    'showcase.audit.col3':  'Actor / Mode',
    'showcase.audit.col4':  'Provenance',

    // use cases
    'uc.idx':           '02 / use cases',
    'uc.title':         'Point it at real work.<br>Get a finished deliverable.',
    'uc.sub':           'Pick a specialist coworker, give it a real task — it doesn\'t just talk, it finishes.',
    'uc.foot':          'Specialist coworkers arrive with tools, working style, and check-ins for one job already set up.',
    'case.a.tag':       '[ security · ships first ]',
    'case.a.title':     'Security review',
    'case.a.desc':      'Scan a codebase for real risk. Findings from deterministic scanners like <span class="mono">semgrep</span> plus model reasoning. Fixes are re-scanned and diff-reviewed before you approve — the fixer is never the only checker.',
    'case.a.note':      'Scan your codebase for real vulnerabilities. Every fix is re-scanned before you approve it.',
    'case.b.tag':       '[ security ]',
    'case.b.title':     'Cloud posture',
    'case.b.desc':      'Audit cloud configuration against common misconfiguration classes, then draft the remediation plan. IAM drift, public buckets, forgotten keys — surfaced with the fix.',
    'case.b.note':      'Audit cloud config, catch common misconfigs, propose fixes.',
    'case.c.tag':       '[ ops ]',
    'case.c.title':     'Incident triage',
    'case.c.desc':      'Work a security or ops incident end-to-end. Gather context across your tools, draft the timeline, prepare the report — while you handle the human parts.',
    'case.c.note':      'Handle incidents: gather context, build timeline, prepare report.',
    'case.d.tag':       '[ everyday ]',
    'case.d.title':     'Everyday work',
    'case.d.desc':      'Prep a customer call from CRM and inbox. Turn scattered notes into a shippable plan. Produce documents, spreadsheets. Keep calendar and Slack threads handled.',
    'case.d.note':      'Prep calls, clear inbox, write docs, manage calendar — like a coworker.',

    // how
    'how.idx':          '02 / how it works',
    'how.title':        'Four moves, one deliverable.',
    'how.sub':          'You state the outcome → it plans → it works → it checks in with you before anything irreversible.',
    'step.1.title':     'Tell the outcome',
    'step.1.desc':      '"prepare a customer brief" · "untangle my calendar" · "check where the release stands across Jira and GitHub"',
    'step.1.note':      'State the result you want, not the steps.',
    'step.2.title':     'It plans and works',
    'step.2.desc':      'Breaks the task into steps. Works across your desktop, files, and connected apps — same tools you\'d open yourself.',
    'step.2.note':      'Splits the task into steps, uses the tools you already use.',
    'step.3.title':     'Checks in before acting',
    'step.3.desc':      'Before anything consequential — sending a message, changing a calendar, running a shell command — it asks. You approve or redirect.',
    'step.3.note':      'Before any irreversible action, it asks first.',
    'step.4.title':     'You get the deliverable',
    'step.4.desc':      'Not a to-do list. Not a summary of what it "would" do. A file, a message sent, a report ready to share.',
    'step.4.note':      'You get the finished result, not a to-do list.',

    // governance
    'gov.idx':          '04 / governance',
    'gov.title':        'Governance is the<br>architecture, <span class="grad-text">not a plugin</span>.',
    'gov.sub':          'The agent can\'t grant itself permissions. No prompt can talk its way past the approval gate.',
    'gov.foot':         'Unattended runs never self-approve. Their asks park in an inbox until a human answers.',
    'tier.1.title':     'Hard floors',
    'tier.1.desc':      'A set of dangerous and irreversible operations is <b>human-only, always</b>. No mode — including full auto-approve — lowers these floors. They always escalate to you.',
    'tier.1.note':      'Dangerous & irreversible actions: <b>always</b> ask a human.',
    'tier.2.title':     'Ladder of earned autonomy',
    'tier.2.desc':      'Actions are approval-gated by default. One-off approvals graduate into standing rules, then into allowlists — each step explicit, visible, and revocable. Repeated denials trip a circuit breaker.',
    'tier.2.note':      'Autonomy grows with approval history, revocable anytime.',
    'tier.3.title':     'Audit trail',
    'tier.3.desc':      'Every tool call is recorded with its approval provenance — auto-approved, user-approved, or denied, with the reviewer\'s reasoning attached — and persisted with the conversation.',
    'tier.3.note':      'Every action logged: who approved, why, and what happened.',

    // compare
    'compare.idx':      '03 / why astra',
    'compare.title':    'Architected for safety & autonomy.<br><span class="grad-text">Not another raw wrapper</span>.',
    'compare.sub':      'Why engineers and security leads choose Astra over web chat boxes or unguarded CLI scripts.',
    'compare.col.feature': 'Capability & Safety Guarantee',
    'compare.col.cloud':   'Cloud Chatbots (ChatGPT / Claude)',
    'compare.col.cli':     'Raw CLI Agents (Unsandboxed)',
    'compare.col.astra':   'Astra AI Coworker',
    'compare.row1.feat':   'Execution Boundary',
    'compare.row1.cloud':  'Remote third-party cloud servers',
    'compare.row1.cli':    'Local terminal, no perimeter',
    'compare.row1.astra':  '100% Local-first sandboxed runtime',
    'compare.row2.feat':   'Safety Enforcement',
    'compare.row2.cloud':  'Probabilistic prompt guardrails only',
    'compare.row2.cli':    'Unchecked shell access, risky scripts',
    'compare.row2.astra':  'Architectural Hard Floors in code',
    'compare.row3.feat':   'Human-in-the-loop Governance',
    'compare.row3.cloud':  'No interactive action approval',
    'compare.row3.cli':    'Terminal prompts interrupt every step',
    'compare.row3.astra':  'Approval Inbox + Earned Autonomy ladder',
    'compare.row4.feat':   'Audit & Action Ledger',
    'compare.row4.cloud':  'Proprietary server logs only',
    'compare.row4.cli':    'Ephemeral terminal scrollback',
    'compare.row4.astra':  'Tamper-evident local provenance ledger',
    'compare.row5.feat':   'Model & Key Independence',
    'compare.row5.cloud':  'Vendor locked to single provider',
    'compare.row5.cli':    'Basic API key support',
    'compare.row5.astra':  'BYOK (Claude, OpenAI, Local Ollama $0)',
    'compare.row6.feat':   'User Interface',
    'compare.row6.cloud':  'Single-column web chat view',
    'compare.row6.cli':    'Terminal CLI only',
    'compare.row6.astra':  'Native 3-pane desktop workstation + CLI',

    // models
    'models.idx':       '04 / bring your own model',
    'models.title':     'Model access is yours.<br>Switch anytime.',
    'models.sub':       'Paste your API key, or run fully local with Ollama. No lock-in to anyone.',
    'models.note':      'A curated model list marks what\'s verified for tool-calling. Adding any model string works — at your own risk.',

    // integrations
    'int.idx':          '05 / integrations',
    'int.title':        '25+ connectors.<br>Plus anything over MCP.',
    'int.sub':          'Astra uses the tools you already use. No need to leave your flow.',
    'int.note':         'Per-tool control. Any tool reachable over the <a href="https://modelcontextprotocol.io/" target="_blank" rel="noopener">Model Context Protocol</a> plugs in.',

    // privacy
    'priv.idx':         '06 / privacy',
    'priv.title':       'Local-first.<br>Your machine, your keys,<br>your data.',
    'priv.sub':         'The full agent loop, conversations, connector tokens, and model keys all live on your machine.',
    'priv.li1':         'Agent loop runs on your device',
    'priv.li2':         'Conversations stay local',
    'priv.li3':         'Connector tokens in local secret store',
    'priv.li4':         'Model keys never leave your machine',
    'priv.li5':         'Only cloud piece: OAuth broker (optional)',
    'priv.lg1':         'stays local',
    'priv.lg2':         'only if you choose',
    'priv.lg3':         'live',

    // approval flow
    'af.idx':           '07 / approval flow',
    'af.title':         'Every consequential action<br>lands as an ask.',
    'af.sub':           'Real example: agent wants to run a shell command → asks for permission → you approve → full log kept.',

    // faq
    'faq.idx':          '07 / frequently asked questions',
    'faq.title':        'Clear answers to critical questions.',
    'faq.sub':          'Everything you need to know about security, architecture, data locality, and pricing.',
    'faq.q1':           'Where does my data, codebase, and API keys go?',
    'faq.a1':           'Everything stays on your physical device. Astra is built local-first: your workspace code, conversation history, connector credentials, and tool executions run locally. API keys are stored in your operating system\'s native credential keychain. If you use Ollama, zero network packets leave your computer.',
    'faq.q2':           'Is Astra completely free and open source?',
    'faq.a2':           'Yes. Astra is 100% open source. There are no paid tier subscriptions, artificial feature gates, or hidden telemetry. You can run from source right now using your own provider keys or local open-weights models with zero expense.',
    'faq.q3':           'How does Astra prevent destructive operations like deleting files?',
    'faq.a3':           'Unlike conventional AI agents that rely on prompt-based instructions, Astra enforces architectural Hard Floors directly in the backend runtime. Irreversible actions (filesystem writes, external webhook triggers, package publishing) mechanically escalate to your human approval inbox, regardless of prompt tampering or autonomy level.',
    'faq.q4':           'Can I connect custom internal tools and databases?',
    'faq.a4':           'Yes. Astra includes 25+ verified connectors out-of-the-box and acts as a native host for the Model Context Protocol (MCP). Any MCP server exposing internal databases, proprietary APIs, or CLI tools can be integrated in seconds with fine-grained permission controls.',
    'faq.q5':           'What platforms and hardware are supported?',
    'faq.a5':           'Astra runs on macOS (Apple Silicon & Intel), Linux (x86_64 / ARM64), and Windows. When using cloud models via API keys, Astra requires minimal CPU and RAM (under 250MB). When running local models via Ollama, we recommend 16GB+ RAM and a modern GPU.',

    // download
    'dl.idx':           '02 / download desktop app',
    'dl.title':         'Get Astra on your workstation.',
    'dl.sub':           'Native desktop app with local engine. Available for macOS, Windows, and Ubuntu/Linux.',
    'dl.rec':           'Detected for your device',
    'dl.mac.desc':      'macOS 12.0 (Monterey) or later. Apple Silicon (M1/M2/M3/M4) & Intel.',
    'dl.mac.arm':       'Apple Silicon (.dmg)',
    'dl.mac.intel':     'Intel x86_64 (.dmg)',
    'dl.win.desc':      'Windows 10 / 11 (64-bit). Native installer with automated path setup.',
    'dl.win.exe':       'Windows Installer (.exe)',
    'dl.win.zip':       'MSI Package (.msi)',
    'dl.linux.desc':    'Ubuntu 20.04+, Debian, Fedora, Arch. Available as .deb package or universal AppImage.',
    'dl.linux.deb':     'Debian / Ubuntu (.deb)',
    'dl.linux.appimage':'AppImage (Universal)',
    'dl.src.tag':       'POWER USERS & DEVELOPERS',
    'dl.src.title':     'Prefer running directly from source?',
    'dl.src.desc':      'Clone repo, set up virtual environment, and launch the Electron frontend with Python coworker in seconds.',

    'ctaband.title':    'Ready to give it real work?',
    'ctaband.sub':      'Run from source now. Installer is coming.',

    // footer
    'footer.tag':       'AI coworker · desktop · local-first.<br>An AI coworker on your machine.',
    'footer.product':   'Product',
    'footer.source':    'Source',
    'footer.builton':   'Built on',
    'footer.author':    'Author',
    'footer.role':      'AI Engineer',
    'footer.legal1':    '© 2026 astra contributors',
    'footer.legal2':    'Personal build · source-first',
    'footer.legal3':    'Made with care, not hype',
  },

  vi: {
    'dl.paused': 'Tải bản cài tạm dừng trong giai đoạn phát triển. Bạn vẫn có thể chạy Astra từ mã nguồn bên dưới.',
    // nav
    'nav.tag':          'v0.1 · bản cá nhân',
    'nav.showcase':     'Xem Ứng Dụng',
    'nav.usecases':     'Kịch bản dùng',
    'nav.how':          'Cách hoạt động',
    'nav.gov':          'Kiểm soát',
    'nav.compare':      'Tại sao chọn Astra',
    'nav.models':       'Model AI',
    'nav.integrations': 'Tích hợp',
    'nav.privacy':      'Riêng tư',
    'nav.approval':     'Luồng phê duyệt',
    'nav.faq':          'FAQ',
    'nav.download':     'Tải Về',
    'theme.dark':       'tối',
    'theme.light':      'sáng',
    'sb.status':        'đang chạy · phiên #a41f',
    'sb.label':         'Mục lục',
    'sb.legal':         '© 2026',

    // hero
    'hero.title':       'Trợ lý AI <em>thật sự</em><br>làm xong việc.',
    'hero.sub':         'Trợ lý AI trên desktop. Bạn giao việc thật — nó trả về code đã fix, tài liệu đã xong, inbox đã dọn. Không phải chat suông.',
    'cta.run':          'Chạy từ source',
    'cta.view':         'Xem source',
    'cta.note':         'Installer sắp có · Cài từ source ngay được',
    'cta.download':     'Tải Astra Desktop',
    'cta.platforms':    'macOS (Apple Silicon & Intel) · Windows 10/11 · Ubuntu/Debian · 100% Miễn phí & Mã nguồn mở',
    'cta.star':         'Star trên GitHub',
    'stat.connectors':  'kết nối',
    'stat.providers':   'nhà cung cấp',
    'stat.locked':      'cloud lock-in',
    'stat.gov':         'tầng kiểm soát',

    // terminal tabs
    'term.tab.security': '01 Quét Bảo Mật',
    'term.tab.cloud':    '02 Audit Cloud',
    'term.tab.incident': '03 Xử Lý Sự Cố',
    'term.tab.cowork':   '04 Việc Hàng Ngày',

    // desktop showcase
    'showcase.idx':     '01 / trải nghiệm tương tác',
    'showcase.title':   'Ứng dụng Desktop đích thực,<br><span class="grad-text">không phải một tab trình duyệt tạm bợ</span>.',
    'showcase.sub':     'Xem Astra chuyển động thực tế trên máy tính: quản lý đa workspace, giải thích reasoning minh bạch, cổng phê duyệt an toàn và kiểm toán local tuyệt đối.',
    'showcase.mode.video':  '▶ Video Trực Quan',
    'showcase.mode.sim':    '⌨ Thao Tác Thử',
    'showcase.tab.chat':    'Tasks & Agent Chat',
    'showcase.tab.inbox':   'Hộp Thư Phê Duyệt',
    'showcase.tab.audit':   'Nhật Ký Kiểm Toán',
    'showcase.badge':       '1 Chờ Duyệt',
    'showcase.agent.role':  'Chuyên Gia Bảo Mật Astra · claude-3.5-sonnet',
    'showcase.agent.status':'Local Core Trực Tuyến · Phiên Cách Ly Sandbox',
    'showcase.task.plan':   'Đang phân tích rủi ro session-fixation trong src/auth/session.ts...',
    'showcase.ask.title':   'Cần Phê Duyệt Từ Con Người',
    'showcase.ask.desc':    'Astra đã chuẩn bị bản vá bảo mật cho lỗ hổng session fixation. Thao tác ghi file này bắt buộc có sự chấp thuận của bạn.',
    'showcase.ask.action':  'Ghi bản vá: src/auth/session.ts (+14 / -8 dòng)',
    'showcase.btn.deny':    'Từ Chối & Chặn',
    'showcase.btn.approve': 'Phê Duyệt & Thực Thi',
    'showcase.audit.title': 'Sổ Cái Hành Động Mật Mã Hóa',
    'showcase.audit.col1':  'Thời Điểm',
    'showcase.audit.col2':  'Hành Động & Mục Tiêu',
    'showcase.audit.col3':  'Tác Tử / Chế Độ',
    'showcase.audit.col4':  'Nguồn Gốc Duyệt',

    // use cases
    'uc.idx':           '02 / kịch bản dùng',
    'uc.title':         'Giao việc thật.<br>Nhận thành phẩm thật.',
    'uc.sub':           'Chọn một chuyên gia (coworker), giao nó task thật — nó không chỉ nói, nó làm xong.',
    'uc.foot':          'Mỗi chuyên gia đến kèm sẵn công cụ, phong cách làm việc và điểm check-in cho một job cụ thể.',
    'case.a.tag':       '[ bảo mật · ra mắt đầu tiên ]',
    'case.a.title':     'Rà soát bảo mật',
    'case.a.desc':      'Quét codebase để tìm rủi ro thật. Kết quả từ scanner tất định như <span class="mono">semgrep</span> kết hợp với lý luận của model. Mỗi bản vá đều được scan lại và duyệt diff trước khi bạn approve — người sửa không bao giờ là người duy nhất kiểm tra.',
    'case.a.note':      'Quét codebase tìm lỗ hổng thật. Fix được scan lại trước khi bạn duyệt.',
    'case.b.tag':       '[ bảo mật ]',
    'case.b.title':     'Kiểm tra cloud',
    'case.b.desc':      'Kiểm tra cấu hình cloud với các lớp misconfiguration phổ biến, rồi soạn sẵn kế hoạch khắc phục. IAM drift, bucket public, key bị quên — được phát hiện kèm bản sửa.',
    'case.b.note':      'Kiểm tra cấu hình cloud, phát hiện lỗi phổ biến, đề xuất bản vá.',
    'case.c.tag':       '[ ops ]',
    'case.c.title':     'Xử lý sự cố',
    'case.c.desc':      'Xử lý sự cố bảo mật/ops end-to-end. Gom context từ mọi công cụ của bạn, dựng timeline, chuẩn bị báo cáo — trong lúc bạn xử lý phần con người.',
    'case.c.note':      'Xử lý sự cố: gom context, dựng timeline, chuẩn bị báo cáo.',
    'case.d.tag':       '[ hàng ngày ]',
    'case.d.title':     'Việc hàng ngày',
    'case.d.desc':      'Chuẩn bị cuộc gọi khách hàng từ CRM và inbox. Biến ghi chú rời rạc thành kế hoạch shippable. Tạo tài liệu, spreadsheet. Trông coi lịch và Slack thread.',
    'case.d.note':      'Chuẩn bị call, dọn inbox, viết doc, quản lý lịch — như một đồng nghiệp.',

    // how
    'how.idx':          '02 / cách hoạt động',
    'how.title':        'Bốn bước, một thành phẩm.',
    'how.sub':          'Bạn nói kết quả muốn có → nó lập kế hoạch → nó chạy → nó kiểm tra lại với bạn trước khi làm điều không thể hoàn tác.',
    'step.1.title':     'Nói kết quả bạn cần',
    'step.1.desc':      '"chuẩn bị brief khách hàng" · "sắp xếp lại lịch của tôi" · "kiểm tra tình trạng release trên Jira và GitHub"',
    'step.1.note':      'Nói rõ kết quả bạn muốn, không cần chỉ từng bước.',
    'step.2.title':     'Nó lập kế hoạch & chạy',
    'step.2.desc':      'Chia task thành các bước. Chạy trên desktop, files và các app đã kết nối — cùng công cụ mà bạn sẽ tự mở.',
    'step.2.note':      'Chia task thành các bước, dùng đúng công cụ bạn đang dùng.',
    'step.3.title':     'Xin phép trước khi hành động',
    'step.3.desc':      'Trước bất kỳ hành động quan trọng nào — gửi tin nhắn, đổi lịch, chạy shell command — nó hỏi. Bạn approve hoặc chỉ hướng khác.',
    'step.3.note':      'Trước mọi hành động không hoàn tác được, nó xin phép.',
    'step.4.title':     'Bạn nhận thành phẩm',
    'step.4.desc':      'Không phải to-do list. Không phải bản tóm tắt "sẽ làm gì". Là một file, một tin nhắn đã gửi, một báo cáo sẵn sàng chia sẻ.',
    'step.4.note':      'Bạn nhận thành phẩm, không phải to-do list.',

    // governance
    'gov.idx':          '04 / kiểm soát',
    'gov.title':        'Kiểm soát là kiến trúc,<br><span class="grad-text">không phải plugin</span>.',
    'gov.sub':          'Agent không thể tự cấp quyền cho chính nó. Không prompt nào lách được cổng phê duyệt.',
    'gov.foot':         'Các phiên chạy không giám sát không tự phê duyệt. Yêu cầu của chúng nằm trong inbox chờ người trả lời.',
    'tier.1.title':     'Sàn cứng (Hard floors)',
    'tier.1.desc':      'Một tập các thao tác nguy hiểm và không hoàn tác là <b>chỉ dành cho người, luôn luôn</b>. Không chế độ nào — kể cả full auto-approve — hạ được sàn này. Chúng luôn eskalasi lên cho bạn.',
    'tier.1.note':      'Việc nguy hiểm & không hoàn tác được: <b>luôn</b> phải hỏi người.',
    'tier.2.title':     'Thang tự chủ có điều kiện',
    'tier.2.desc':      'Mọi hành động mặc định cần duyệt. Duyệt một lần có thể trở thành rule thường trực, rồi thành allowlist — mỗi bước rõ ràng, thấy được, thu hồi được. Bị từ chối liên tục sẽ ngắt aptomat.',
    'tier.2.note':      'Quyền tự chủ tăng dần theo lịch sử phê duyệt, có thể thu hồi bất cứ lúc nào.',
    'tier.3.title':     'Nhật ký kiểm toán',
    'tier.3.desc':      'Mọi tool call được ghi lại kèm nguồn gốc phê duyệt — auto, người dùng, hoặc bị từ chối, kèm lý do của reviewer — và lưu cùng hội thoại.',
    'tier.3.note':      'Mọi hành động đều được log lại: ai duyệt, vì sao, kết quả ra sao.',

    // compare
    'compare.idx':      '03 / tại sao chọn astra',
    'compare.title':    'Kiến trúc vì an toàn & quyền tự chủ.<br><span class="grad-text">Không phải một wrapper sơ sài</span>.',
    'compare.sub':      'Lý do các kỹ sư và chuyên gia bảo mật lựa chọn Astra thay vì các khung chat web hay script CLI mất kiểm soát.',
    'compare.col.feature': 'Tính Năng & Cam Kết An Toàn',
    'compare.col.cloud':   'Cloud Chatbot (ChatGPT / Claude)',
    'compare.col.cli':     'Agent CLI Tự Do (Không Sandbox)',
    'compare.col.astra':   'Đồng Nghiệp AI Astra',
    'compare.row1.feat':   'Phạm Vi Thực Thi',
    'compare.row1.cloud':  'Máy chủ đám mây của bên thứ ba',
    'compare.row1.cli':    'Terminal cục bộ, không rào chắn',
    'compare.row1.astra':  '100% Local-first trong sandbox an toàn',
    'compare.row2.feat':   'Cơ Chế Bảo Vệ',
    'compare.row2.cloud':  'Chỉ dựa vào prompt hướng dẫn xác suất',
    'compare.row2.cli':    'Quyền shell tùy tiện, rủi ro cao',
    'compare.row2.astra':  'Sàn cứng (Hard Floors) bằng code lõi',
    'compare.row3.feat':   'Kiểm Soát Phê Duyệt Con Người',
    'compare.row3.cloud':  'Không có luồng duyệt hành động',
    'compare.row3.cli':    'Prompt hỏi liên tục làm ngắt quãng',
    'compare.row3.astra':  'Hộp thư Inbox + Thang tự chủ tăng dần',
    'compare.row4.feat':   'Sổ Cái Nhật Ký Kiểm Toán',
    'compare.row4.cloud':  'Chỉ có log nội bộ của nhà cung cấp',
    'compare.row4.cli':    'Cuộn terminal tạm bợ, dễ mất',
    'compare.row4.astra':  'Sổ cái nguồn gốc minh bạch, chống giả mạo',
    'compare.row5.feat':   'Tự Chủ Model & API Key',
    'compare.row5.cloud':  'Bị khóa cứng vào một nhà cung cấp',
    'compare.row5.cli':    'Hỗ trợ API key đơn giản',
    'compare.row5.astra':  'BYOK (Claude, OpenAI, Ollama Local $0)',
    'compare.row6.feat':   'Giao Diện Trải Nghiệm',
    'compare.row6.cloud':  'Khung chat một cột đơn điệu',
    'compare.row6.cli':    'Chỉ có màn hình đen terminal',
    'compare.row6.astra':  'Desktop workstation 3 cột + CLI',

    // models
    'models.idx':       '04 / dùng model của bạn',
    'models.title':     'Model là của bạn.<br>Đổi bất cứ lúc nào.',
    'models.sub':       'Dán API key của bạn, hoặc chạy hoàn toàn local với Ollama. Không lock vào ai.',
    'models.note':      'Danh sách model được curate đánh dấu cái nào đã verify cho tool-calling. Thêm bất kỳ model string nào cũng chạy — bạn tự chịu rủi ro.',

    // integrations
    'int.idx':          '05 / tích hợp',
    'int.title':        '25+ kết nối.<br>Cộng bất kỳ gì qua MCP.',
    'int.sub':          'Astra dùng đúng công cụ bạn đang dùng. Không cần rời khỏi flow.',
    'int.note':         'Kiểm soát theo từng tool. Bất kỳ tool nào tiếp cận được qua <a href="https://modelcontextprotocol.io/" target="_blank" rel="noopener">Model Context Protocol</a> đều cắm vào được.',

    // privacy
    'priv.idx':         '06 / riêng tư',
    'priv.title':       'Local-first.<br>Máy bạn, key của bạn,<br>data của bạn.',
    'priv.sub':         'Toàn bộ agent loop, hội thoại, token connector, model key đều nằm trên máy bạn.',
    'priv.li1':         'Agent loop chạy trên thiết bị của bạn',
    'priv.li2':         'Hội thoại nằm local',
    'priv.li3':         'Token connector trong secret store cục bộ',
    'priv.li4':         'Model key không rời khỏi máy bạn',
    'priv.li5':         'Chỉ có 1 phần cloud: OAuth broker (tùy chọn)',
    'priv.lg1':         'nằm local',
    'priv.lg2':         'chỉ khi bạn cho phép',
    'priv.lg3':         'trực tiếp',

    // approval flow
    'af.idx':           '07 / luồng phê duyệt',
    'af.title':         'Mọi hành động quan trọng<br>đều là một yêu cầu.',
    'af.sub':           'Ví dụ thực tế: agent muốn chạy lệnh shell → xin phép → bạn duyệt → có log đầy đủ.',

    // faq
    'faq.idx':          '07 / câu hỏi thường gặp',
    'faq.title':        'Giải đáp rõ ràng mọi thắc mắc.',
    'faq.sub':          'Những điều bạn cần biết về bảo mật, kiến trúc, vị trí lưu trữ dữ liệu và chi phí.',
    'faq.q1':           'Dữ liệu, mã nguồn và API key của tôi sẽ đi đâu?',
    'faq.a1':           'Toàn bộ đều nằm lại trên chính máy tính của bạn. Astra thiết kế theo triết lý local-first: mã nguồn dự án, lịch sử hội thoại, thông tin kết nối và việc thực thi công cụ đều xử lý cục bộ. API key được lưu an toàn trong Keychain của hệ điều hành. Nếu dùng Ollama, không có bất kỳ byte dữ liệu nào rời khỏi máy.',
    'faq.q2':           'Astra có hoàn toàn miễn phí và mã nguồn mở không?',
    'faq.a2':           'Đúng vậy. Astra hoàn toàn mã nguồn mở 100%. Không có gói thu phí định kỳ, không giới hạn tính năng nhân tạo, không thu thập dữ liệu ngầm. Bạn có thể tự chạy từ source ngay bây giờ, sử dụng API key cá nhân hoặc mô hình local miễn phí 100%.',
    'faq.q3':           'Làm thế nào Astra ngăn các hành vi phá hoại như xóa file?',
    'faq.a3':           'Khác với các agent thông thường chỉ ngăn chặn bằng câu lệnh prompt, Astra thiết lập Sàn Cứng (Hard Floors) bằng mã nguồn kiểm soát thực thi ở tầng backend. Các hành động rủi ro cao không thể hoàn tác (ghi file, gửi request ra ngoài, xuất bản package) bắt buộc phải chuyển qua hộp thư phê duyệt của bạn.',
    'faq.q4':           'Tôi có thể kết nối với công cụ và cơ sở dữ liệu nội bộ không?',
    'faq.a4':           'Hoàn toàn được. Astra hỗ trợ sẵn hơn 25 connector phổ biến và đóng vai trò là một máy chủ MCP (Model Context Protocol). Bất kỳ server MCP nào kết nối DB nội bộ, API riêng hay CLI tools đều có thể cắm vào nhanh chóng với phân quyền minh bạch.',
    'faq.q5':           'Astra hỗ trợ những nền tảng và phần cứng nào?',
    'faq.a5':           'Astra hỗ trợ đầy đủ macOS (Apple Silicon & Intel), Linux (x86_64 / ARM64), và Windows. Khi dùng model cloud qua API key, Astra chỉ tốn rất ít tài nguyên (dưới 250MB RAM). Khi chạy mô hình local qua Ollama, khuyến nghị máy có từ 16GB RAM trở lên và có card đồ họa hỗ trợ.',

    // download
    'dl.idx':           '02 / tải ứng dụng desktop',
    'dl.title':         'Cài đặt Astra trên máy trạm của bạn.',
    'dl.sub':           'Ứng dụng desktop native kèm engine cục bộ. Hỗ trợ macOS, Windows và Ubuntu/Linux.',
    'dl.rec':           'Phù hợp với thiết bị của bạn',
    'dl.mac.desc':      'macOS 12.0 (Monterey) trở lên. Hỗ trợ Apple Silicon (M1/M2/M3/M4) và chip Intel.',
    'dl.mac.arm':       'Apple Silicon (.dmg)',
    'dl.mac.intel':     'Intel x86_64 (.dmg)',
    'dl.win.desc':      'Windows 10 / 11 (64-bit). Trình cài đặt native tự động cấu hình PATH môi trường.',
    'dl.win.exe':       'Bộ cài Windows (.exe)',
    'dl.win.zip':       'Gói cài đặt MSI (.msi)',
    'dl.linux.desc':    'Ubuntu 20.04+, Debian, Fedora, Arch. Đóng gói dạng file .deb hoặc AppImage chạy trực tiếp.',
    'dl.linux.deb':     'Debian / Ubuntu (.deb)',
    'dl.linux.appimage':'Bản AppImage (Mọi distro)',
    'dl.src.tag':       'DÀNH CHO LẬP TRÌNH VIÊN & CHUYÊN GIA',
    'dl.src.title':     'Bạn muốn chạy trực tiếp từ mã nguồn?',
    'dl.src.desc':      'Clone repo, tạo môi trường ảo Python và khởi chạy giao diện Electron cùng AI coworker trong 2 phút.',

    'ctaband.title':    'Sẵn sàng giao việc thật?',
    'ctaband.sub':      'Cài từ source ngay được. Installer sẽ có sau.',

    // footer
    'footer.tag':       'Trợ lý AI · desktop · local-first.<br>Trợ lý AI trên máy bạn.',
    'footer.product':   'Sản phẩm',
    'footer.source':    'Mã nguồn',
    'footer.builton':   'Xây trên',
    'footer.author':    'Tác giả',
    'footer.role':      'AI Engineer',
    'footer.legal1':    '© 2026 astra contributors',
    'footer.legal2':    'Bản cá nhân · source-first',
    'footer.legal3':    'Làm bằng tâm huyết, không phải hype',
  },
};

(function langToggle() {
  const html = document.documentElement;
  const btn = document.getElementById('langToggle');

  function apply(lang) {
    const dict = I18N[lang] || I18N.en;
    document.querySelectorAll('[data-i18n]').forEach((el) => {
      const key = el.getAttribute('data-i18n');
      if (dict[key] != null) el.textContent = dict[key];
    });
    document.querySelectorAll('[data-i18n-html]').forEach((el) => {
      const key = el.getAttribute('data-i18n-html');
      if (dict[key] != null) el.innerHTML = dict[key];
    });
    html.setAttribute('lang', lang);
    html.setAttribute('data-lang', lang);
    if (typeof window.updateDownloadUIForDevice === 'function') {
      window.updateDownloadUIForDevice();
    }
  }

  // Initial: read from localStorage or fall back to 'en' (page ships in EN markup)
  const saved = localStorage.getItem('astra-lang');
  const initial = (saved === 'vi' || saved === 'en') ? saved : 'en';
  apply(initial);

  if (!btn) return;
  btn.addEventListener('click', () => {
    const current = html.getAttribute('data-lang') || 'en';
    const next = current === 'en' ? 'vi' : 'en';
    localStorage.setItem('astra-lang', next);
    apply(next);
  });
})();
