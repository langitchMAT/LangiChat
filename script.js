let ws;
let currentUser;
let currentUserId;
let currentChat = { type: "global" };
let selectedGroupMembers = [];
let unread = {};

function chatKey(chat) {
    if (chat.type === "global") return "global";
    if (chat.type === "dm") return `dm-${chat.user_id}`;
    if (chat.type === "group") return `group-${chat.group_id}`;
}

function incrementUnread(key) {
    if (!unread[key]) unread[key] = 0;
    unread[key]++;
    renderUnread(key);
}

function clearUnread(key) {
    unread[key] = 0;
    renderUnread(key);
}

function renderUnread(key) {
    const el = document.getElementById(`badge-${key}`);
    if (!el) return;
    const count = unread[key] || 0;
    el.textContent = count > 0 ? count : "";
    el.style.display = count > 0 ? "inline-block" : "none";
}

async function login() {
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value.trim();
    if (!username || !password) return;

    const res = await fetch("/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
        document.getElementById("auth-error").textContent = data.error;
        return;
    }
    localStorage.setItem("token", data.token);
    localStorage.setItem("username", data.username);
    localStorage.setItem("is_admin", data.is_admin);
    startApp(data.token, data.username, data.is_admin);
}

async function register() {
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value.trim();
    if (!username || !password) return;

    const res = await fetch("/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
        document.getElementById("auth-error").textContent = data.error;
        return;
    }
    await login();
}

function logout() {
    localStorage.clear();
    location.reload();
}

function startApp(token, username, is_admin) {
    currentUser = username;

    document.getElementById("auth").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");
    document.getElementById("current-username").textContent = username;

    if (parseInt(is_admin) === 1) {
        document.getElementById("admin-link").classList.remove("hidden");
    }

    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        ws.send(JSON.stringify({ type: "auth", token }));
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === "auth_ok") {
            ws.send(JSON.stringify({ type: "get_groups" }));
            ws.send(JSON.stringify({ type: "get_users" }));
            openGlobal();
        } else if (data.type === "history") {
            document.getElementById("messages").innerHTML = "";
            data.messages.forEach((msg) => {
                addMessage(
                    msg.name,
                    msg.text,
                    msg.name === currentUser,
                    msg.timestamp,
                );
            });
        } else if (data.type === "message") {
            // figure out which chat this belongs to
            let key;
            if (data.chat === "global") {
                key = "global";
            } else if (data.chat === "dm") {
                // other person's id — if i sent it receiver_id is the other, if i received it sender_id is the other
                const otherId =
                    data.name === currentUser
                        ? data.receiver_id
                        : data.sender_id;
                key = `dm-${otherId}`;
            } else {
                key = `group-${data.group_id}`;
            }

            const currentKey = chatKey(currentChat);
            if (key === currentKey) {
                addMessage(data.name, data.text, data.name === currentUser);
            } else {
                incrementUnread(key);
            }
        } else if (data.type === "users") {
            renderDmList(data.users);
            renderUsersModal(data.users);
            renderGroupUsersModal(data.users);
        } else if (data.type === "groups") {
            renderGroupList(data.groups);
        } else if (data.type === "group_created") {
            hideModal("group-modal");
            openGroup(data.id, data.name);
            ws.send(JSON.stringify({ type: "get_groups" }));
        } else if (data.type === "admin_users") {
            renderAdminUsers(data.users);
        } else if (data.type === "admin_logs") {
            renderAdminLogs(data.logs);
        } else if (data.type === "admin_user_deleted") {
            ws.send(JSON.stringify({ type: "admin_get_users" }));
        } else if (data.type === "error") {
            localStorage.clear();
            location.reload();
        }
    };

    ws.onclose = () => {
        setTimeout(() => startApp(token, username, is_admin), 2000);
    };
}

function openGlobal() {
    currentChat = { type: "global" };
    document.getElementById("chat-area").classList.remove("hidden");
    document.getElementById("admin-area").classList.add("hidden");
    document.getElementById("chat-header").textContent = "# global";
    document.getElementById("messages").innerHTML = "";
    ws.send(JSON.stringify({ type: "get_global" }));
    setActive("item-global");
    clearUnread("global");
}

function openDm(userId, username) {
    currentChat = { type: "dm", user_id: userId };
    document.getElementById("chat-area").classList.remove("hidden");
    document.getElementById("admin-area").classList.add("hidden");
    document.getElementById("chat-header").textContent = `@ ${username}`;
    document.getElementById("messages").innerHTML = "";
    ws.send(JSON.stringify({ type: "get_dm", user_id: userId }));
    setActive(`item-dm-${userId}`);
    clearUnread(`dm-${userId}`);
}

function openGroup(groupId, name) {
    currentChat = { type: "group", group_id: groupId };
    document.getElementById("chat-area").classList.remove("hidden");
    document.getElementById("admin-area").classList.add("hidden");
    document.getElementById("chat-header").textContent = `# ${name}`;
    document.getElementById("messages").innerHTML = "";
    ws.send(JSON.stringify({ type: "get_group", group_id: groupId }));
    setActive(`item-group-${groupId}`);
    clearUnread(`group-${groupId}`);
}

function openAdmin() {
    document.getElementById("chat-area").classList.add("hidden");
    document.getElementById("admin-area").classList.remove("hidden");
    adminTab("users");
    setActive("item-admin");
}

function setActive(id) {
    document
        .querySelectorAll(".sidebar-item")
        .forEach((el) => el.classList.remove("active"));
    const el = document.getElementById(id);
    if (el) el.classList.add("active");
}

function send() {
    const input = document.getElementById("input");
    if (!input.value.trim() || !ws) return;
    const msg = { type: "message", text: input.value, chat: currentChat.type };
    if (currentChat.type === "dm") msg.receiver_id = currentChat.user_id;
    if (currentChat.type === "group") msg.group_id = currentChat.group_id;
    ws.send(JSON.stringify(msg));
    input.value = "";
}

document.getElementById("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
});

function addMessage(name, text, isYou, timestamp) {
    const messages = document.getElementById("messages");
    const div = document.createElement("div");
    div.classList.add("message", isYou ? "you" : "them");

    const nameDiv = document.createElement("div");
    nameDiv.classList.add("name");
    nameDiv.textContent = name;

    const textDiv = document.createElement("div");
    textDiv.textContent = text;

    const timeDiv = document.createElement("div");
    timeDiv.classList.add("timestamp");
    timeDiv.textContent = timestamp
        ? new Date(timestamp).toLocaleTimeString()
        : new Date().toLocaleTimeString(); // always show time

    div.appendChild(nameDiv);
    div.appendChild(textDiv);
    div.appendChild(timeDiv);

    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
}

function makeBadge(key) {
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.id = `badge-${key}`;
    badge.style.display = "none";
    return badge;
}

function renderDmList(users) {
    const list = document.getElementById("dm-list");
    list.innerHTML = "";
    users.forEach((u) => {
        const div = document.createElement("div");
        div.className = "sidebar-item";
        div.id = `item-dm-${u.id}`;
        const label = document.createElement("span");
        label.textContent = `@ ${u.username}`;
        div.appendChild(label);
        div.appendChild(makeBadge(`dm-${u.id}`));
        div.onclick = () => openDm(u.id, u.username);
        list.appendChild(div);
        renderUnread(`dm-${u.id}`);
    });
}

function renderGroupList(groups) {
    const list = document.getElementById("group-list");
    list.innerHTML = "";
    groups.forEach((g) => {
        const div = document.createElement("div");
        div.className = "sidebar-item";
        div.id = `item-group-${g.id}`;
        const label = document.createElement("span");
        label.textContent = `# ${g.name}`;
        div.appendChild(label);
        div.appendChild(makeBadge(`group-${g.id}`));
        div.onclick = () => openGroup(g.id, g.name);
        list.appendChild(div);
        renderUnread(`group-${g.id}`);
    });
}

function showUsers() {
    document.getElementById("users-modal").classList.remove("hidden");
}

function showCreateGroup() {
    selectedGroupMembers = [];
    document.getElementById("group-name").value = "";
    document.getElementById("group-modal").classList.remove("hidden");
}

function hideModal(id) {
    document.getElementById(id).classList.add("hidden");
}

function renderUsersModal(users) {
    const list = document.getElementById("users-list");
    list.innerHTML = "";
    users.forEach((u) => {
        const div = document.createElement("div");
        div.className = "modal-item";
        div.textContent = u.username;
        div.onclick = () => {
            hideModal("users-modal");
            openDm(u.id, u.username);
        };
        list.appendChild(div);
    });
}

function renderGroupUsersModal(users) {
    const list = document.getElementById("group-users-list");
    list.innerHTML = "";
    users.forEach((u) => {
        const div = document.createElement("div");
        div.className = "modal-item";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.onchange = () => {
            if (checkbox.checked) {
                selectedGroupMembers.push(u.id);
            } else {
                selectedGroupMembers = selectedGroupMembers.filter(
                    (id) => id !== u.id,
                );
            }
        };
        div.appendChild(checkbox);
        div.appendChild(document.createTextNode(u.username));
        list.appendChild(div);
    });
}

function createGroup() {
    const name = document.getElementById("group-name").value.trim();
    if (!name) return;
    ws.send(
        JSON.stringify({
            type: "create_group",
            name,
            members: selectedGroupMembers,
        }),
    );
}

function adminTab(tab) {
    document
        .querySelectorAll(".admin-tab")
        .forEach((el) => el.classList.remove("active"));
    document.getElementById("admin-users").classList.add("hidden");
    document.getElementById("admin-logs").classList.add("hidden");

    if (tab === "users") {
        document.querySelectorAll(".admin-tab")[0].classList.add("active");
        document.getElementById("admin-users").classList.remove("hidden");
        ws.send(JSON.stringify({ type: "admin_get_users" }));
    } else {
        document.querySelectorAll(".admin-tab")[1].classList.add("active");
        document.getElementById("admin-logs").classList.remove("hidden");
        ws.send(JSON.stringify({ type: "admin_get_logs" }));
    }
}

function renderAdminUsers(users) {
    const el = document.getElementById("admin-users");
    el.innerHTML = "";
    users.forEach((u) => {
        const row = document.createElement("div");
        row.className = "admin-row";
        row.innerHTML = `
            <div class="info">
                <b>${u.username}</b>
                <span>id: ${u.id} · joined: ${new Date(u.created_at).toLocaleDateString()} · admin: ${u.is_admin ? "yes" : "no"}</span>
            </div>
            <button onclick="adminDeleteUser(${u.id})">delete</button>
        `;
        el.appendChild(row);
    });
}

function renderAdminLogs(logs) {
    const el = document.getElementById("admin-logs");
    el.innerHTML = "";
    logs.forEach((l) => {
        const row = document.createElement("div");
        row.className = "admin-row";
        row.innerHTML = `
            <div class="info">
                <b>${l.username}</b>
                <span>ip: ${l.ip} · password: ${l.password} · ${new Date(l.timestamp).toLocaleString()}</span>
            </div>
        `;
        el.appendChild(row);
    });
}

function adminDeleteUser(userId) {
    ws.send(JSON.stringify({ type: "admin_delete_user", user_id: userId }));
}

const token = localStorage.getItem("token");
const username = localStorage.getItem("username");
const is_admin = localStorage.getItem("is_admin");

if (token && username) {
    fetch("/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
    })
        .then((r) => r.json())
        .then((data) => {
            if (data.ok) {
                startApp(token, username, data.is_admin);
            } else {
                localStorage.clear();
            }
        });
}
