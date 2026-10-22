# Astra Team: Intelligent Multi-Agent Cognitive Engine Plan

## 1. Goal & Architecture Objective
Nâng cấp hệ thống Astra Team từ mô hình "Dispatcher thụ động, cô lập cứng nhắc" thành một **Hệ Điều Hành Multi-Agent Thông Minh (Cognitive Multi-Agent OS)**:
1. **Agent-to-Agent Conversational Collaboration**: Cho phép các Agent trong Group Chat tương tác, phản biện, phân chia công việc tự nhiên dưới sự kiểm soát của `LoopGuard`.
2. **Subagent Delegation Tool (`team_ask_teammate`)**: Cho phép một Agent đang thực thi có thể hỏi ý kiến hoặc ủy thác cho một Agent đồng nghiệp (chuyên gia) và nhận kết quả tức thì trong cùng một lượt suy luận (Sync/Subroutine RPC).
3. **Cognitive Multi-Role Context Assembly**: Chuyển đổi từ cơ chế dump text `[Tên]` thô thiển sang semantic roles chuẩn (`role: "assistant", name: "AgentName"`) để các LLM hàng đầu (GPT-4o, Claude 3.7, Gemini 2.5) tối ưu attention và hiểu sâu sắc vai vế của từng teammate.
4. **Task & Team Blackboard (Shared Ephemeral Working State)**: Cung cấp vùng nhớ chia sẻ giữa các agent cùng giải quyết một task hoặc thread (diffs, discovered facts, sub-checklists).

---

## 2. Phân Chia Task & Kế Hoạch Triển Khai (TDD-first)

### Task 1: Semantic Roles & Multi-Agent Prompt Formatting (`coworker/teams/context.py` & `coworker/teams/runtime.py`)
- **Vấn đề**: Hiện tại `_render_package` biến mọi tin nhắn của các bot khác thành `role: "user"`, khiến LLM bị loạn ngữ cảnh và không nhận thức được teammate.
- **Giải pháp**:
  - Chuẩn hóa format lịch sử tin nhắn: Các tin nhắn từ Agent khác sẽ mang `role: "assistant"`, kèm `name: <agent_name_normalized>` (theo chuẩn OpenAI/Anthropic format).
  - Tách rõ ràng giữa: Lệnh của Human (`role: "user"`), Kết quả/Phát biểu của Teammate (`role: "assistant", name: ...`), và System Boundaries.
- **Test**: `tests/test_team_context_formatting.py` kiểm tra phân loại đúng role, name và format.

### Task 2: Agent-to-Agent Conversational Routing & Loop Guard (`coworker/teams/routing.py`)
- **Vấn đề**: `routing.py` cấm tiệt bot gửi tin nhắn kích hoạt bot khác (`sender.kind != HUMAN -> NO_TARGET`).
- **Giải pháp**:
  - Hỗ trợ routing khi Agent `@mention` một Agent đồng nghiệp trong group chat.
  - Tích hợp `LoopGuard` và `max_agent_turns` (mặc định tối đa 4 lượt liên tiếp bot-to-bot trong một luồng trước khi tạm dừng chờ Human).
  - Ngăn chặn vòng lặp bot tự gọi chính nó (`self-mention`) hoặc bot gọi nhau vô tận (A -> B -> A -> B...).
- **Test**: `tests/test_team_routing_agent_interaction.py`.

### Task 3: In-Run Teammate RPC Tool (`team_ask_teammate` trong `coworker/teams/capabilities.py`)
- **Vấn đề**: Handoff hiện tại là ném task đi rồi tắt máy (`fire-and-forget`). Agent không thể nhờ vả nhau rồi làm tiếp.
- **Giải pháp**:
  - Thêm tool `team_ask_teammate(agent_id, query, task_id=None)`:
    - Cho phép Agent A tạm ngưng, dispatch một sub-work request tới Agent B với context gọn.
    - Chờ Agent B trả về kết quả dưới dạng `ChildResultEnvelope`.
    - Trả kết quả đó về làm `tool_result` cho Agent A trong cùng turn để A xử lý tiếp.
- **Test**: `tests/test_team_subagent_tool.py`.

### Task 4: Task Working Memory / Blackboard (`coworker/teams/store.py` & `memory.py`)
- **Vấn đề**: Không có vùng nhớ chia sẻ trạng thái làm việc động giữa các agent.
- **Giải pháp**:
  - Tạo `WorkingStateStore` hỗ trợ key-value, artifact pointer và live checklist theo `task_id` / `thread_id`.
  - Cung cấp tool `team_blackboard(action: get|set|list, key, value)` để các agent cập nhật tiến độ cho nhau.
- **Test**: `tests/test_team_blackboard.py`.

---

## 3. Quản Trị Rủi Ro & Bảo Toàn Invariant Của Astra
- Không phá vỡ quy tắc: Mỗi persistent agent vẫn có Sandbox riêng.
- Giữ nguyên các hợp đồng cốt lõi: `WorkRequest`, `Delivery`, `Lease`, `Audit Log`.
- Toàn bộ tests cũ phải pass 100%.
