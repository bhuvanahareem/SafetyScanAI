/**
 * SAFETY SCAN AI — COMMAND CENTER SPA LOGIC
 */

// 1. GLOBAL STATE
let state = {
  view: 'auth', // auth, setup, monitor, analytics
  user: JSON.parse(localStorage.getItem('ss_user')) || null,
  activeSector: null,
  sectors: [],
  violationCount: 0,
  monitorInterval: null,
  isAnalyzing: false,
  ws: null
};

// 2. INITIALIZATION
document.addEventListener('DOMContentLoaded', () => {
  initSPA();
  startClock();
  checkSystemHealth();
  setInterval(checkSystemHealth, 15000);
});

function initSPA() {
  if (state.user) {
    autoSwitchToMain();
  } else {
    showView('auth');
  }

  setupEventListeners();
}

/** Determines if we go to Monitor or Setup based on sectors existence */
async function autoSwitchToMain() {
  try {
    const res = await fetch(`/sectors/${state.user.user_id}`);
    const sectors = await res.json();
    if (sectors && sectors.length > 0) {
      state.sectors = sectors;
      state.activeSector = sectors[0];
      showView('monitor');
      renderSectors();
      connectWebSocket();
    } else {
      showView('setup');
    }
  } catch (err) {
    showView('auth');
  }
}

// 3. UI NAVIGATION
function showView(viewId) {
  state.view = viewId;
  const views = ['auth', 'setup', 'monitor', 'analytics'];
  views.forEach(v => {
    const el = document.getElementById(`view-${v}`);
    if (el) el.classList.add('hidden');
  });

  const activeView = document.getElementById(`view-${viewId}`);
  if (activeView) activeView.classList.remove('hidden');

  // Sidebar visibility
  const sidebar = document.getElementById('global-sidebar');
  if (viewId === 'monitor' || viewId === 'analytics') {
    sidebar.classList.remove('hidden');
    updateSidebarActiveState(viewId);
  } else {
    sidebar.classList.add('hidden');
  }

  // View specific init
  if (viewId === 'monitor') initMonitor();
  if (viewId === 'analytics') loadAnalytics();
}

function updateSidebarActiveState(view) {
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === view);
  });
}

// 4. EVENT LISTENERS
function setupEventListeners() {
  // Auth Tabs
  document.getElementById('auth-tab-login').onclick = () => toggleAuthMode('login');
  document.getElementById('auth-tab-register').onclick = () => toggleAuthMode('register');

  // Login Form
  document.getElementById('login-form').onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    try {
      const res = await fetch('/login', { method: 'POST', body: formData });
      if (res.ok) {
        const data = await res.json();
        handleAuthSuccess(data);
      } else {
        showAuthError('Invalid credentials or server error.');
      }
    } catch (err) { showAuthError('Connection failed.'); }
  };

  // Register Form
  document.getElementById('register-form').onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(e.target);
    try {
      const res = await fetch('/register', { method: 'POST', body: formData });
      if (res.ok) {
        const data = await res.json();
        handleAuthSuccess(data);
      } else { showAuthError('Registration failed.'); }
    } catch (err) { showAuthError('Connection failed.'); }
  };

  // Setup Flow
  document.getElementById('generate-cards-btn').onclick = () => renderSetupCards();
  document.getElementById('submit-setup-btn').onclick = finalizeSiteSetup;

  // Sidebar Nav
  document.querySelectorAll('.nav-item').forEach(btn => {
    btn.onclick = () => showView(btn.dataset.view);
  });

  // Logout
  document.getElementById('logout-btn').onclick = () => {
    localStorage.removeItem('ss_user');
    location.reload();
  };

  // Analytics
  document.getElementById('refresh-analytics').onclick = loadAnalytics;
  document.getElementById('clear-alerts').onclick = () => {
    document.getElementById('violation-stack').innerHTML = `
      <div id="empty-alerts" class="h-full flex flex-col items-center justify-center text-center opacity-30 grayscale">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#DFBC94" stroke-width="1"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
        <p class="italic font-display text-xl mt-4">Static Environment Clean</p>
      </div>
    `;
    state.violationCount = 0;
    document.getElementById('violation-count').textContent = '0';
  };
}

// 5. AUTH LOGIC
function toggleAuthMode(mode) {
  const loginForm = document.getElementById('login-form');
  const registerForm = document.getElementById('register-form');
  const loginTab = document.getElementById('auth-tab-login');
  const registerTab = document.getElementById('auth-tab-register');

  if (mode === 'login') {
    loginForm.classList.remove('hidden');
    registerForm.classList.add('hidden');
    loginTab.className = "flex-1 py-2 text-sm font-semibold rounded-lg bg-gold/10 text-gold transition-all";
    registerTab.className = "flex-1 py-2 text-sm font-semibold rounded-lg text-gold/40 hover:text-gold/80 transition-all";
  } else {
    loginForm.classList.add('hidden');
    registerForm.classList.remove('hidden');
    registerTab.className = "flex-1 py-2 text-sm font-semibold rounded-lg bg-gold/10 text-gold transition-all";
    loginTab.className = "flex-1 py-2 text-sm font-semibold rounded-lg text-gold/40 hover:text-gold/80 transition-all";
  }
}

function handleAuthSuccess(data) {
  state.user = data;
  localStorage.setItem('ss_user', JSON.stringify(data));
  document.getElementById('admin-display-name').textContent = data.admin_name;
  autoSwitchToMain();
}

function showAuthError(msg) {
  const err = document.getElementById('auth-error');
  err.textContent = msg;
  err.classList.remove('hidden');
  setTimeout(() => err.classList.add('hidden'), 3000);
}

// 6. SETUP LOGIC
function renderSetupCards() {
  const count = parseInt(document.getElementById('sector-count-input').value) || 1;
  const grid = document.getElementById('sector-cards-grid');
  grid.innerHTML = '';

  for (let i = 1; i <= count; i++) {
    const card = document.createElement('div');
    card.className = "bg-ink-light border border-gold/10 p-6 rounded-2xl space-y-4 animate-fade-in";
    card.innerHTML = `
      <div class="flex items-center justify-between">
        <span class="text-[10px] uppercase tracking-widest text-vermillion font-bold">Sector 0${i}</span>
        <div class="w-8 h-8 rounded-full bg-gold/5 flex items-center justify-center text-gold italic">0${i}</div>
      </div>
      <input type="text" class="sector-name w-full bg-ink border border-gold/5 rounded-xl px-4 py-3 text-sm" placeholder="Sector Name (e.g. Lab A)" required />
      <div class="grid grid-cols-2 gap-3">
        <input type="text" class="supervisor-name w-full bg-ink border border-gold/5 rounded-xl px-4 py-2 text-xs" placeholder="Supervisor Name" required />
        <input type="email" class="supervisor-email w-full bg-ink border border-gold/5 rounded-xl px-4 py-2 text-xs" placeholder="Supervisor Email" required />
      </div>
      <div class="relative h-24 rounded-xl border border-dashed border-gold/20 flex flex-col items-center justify-center bg-ink group hover:border-vermillion/50 transition-colors">
        <input type="file" class="sector-video absolute inset-0 opacity-0 cursor-pointer" accept="video/*" />
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#DFBC94" stroke-width="1.5" class="group-hover:stroke-vermillion"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
        <span class="text-[10px] text-gold/30 mt-2 file-name">Upload CCTV Source</span>
      </div>
    `;
    
    // File name update listener
    const fileInput = card.querySelector('.sector-video');
    fileInput.onchange = (e) => {
      const name = e.target.files[0]?.name || "Upload CCTV Source";
      card.querySelector('.file-name').textContent = name;
    };

    grid.appendChild(card);
  }
}

async function finalizeSiteSetup() {
  const cards = document.querySelectorAll('#sector-cards-grid > div');
  if (cards.length === 0) return alert('Please define at least one sector.');

  const sectorsToUpload = [];
  const submitBtn = document.getElementById('submit-setup-btn');
  submitBtn.disabled = true;
  submitBtn.textContent = "Deploying Assets...";

  try {
    for (const card of cards) {
      const name = card.querySelector('.sector-name').value;
      const sName = card.querySelector('.supervisor-name').value;
      const sEmail = card.querySelector('.supervisor-email').value;
      const videoFile = card.querySelector('.sector-video').files[0];

      if (!name || !sName || !sEmail || !videoFile) throw new Error('All fields are required.');

      // Upload video first
      const videoFormData = new FormData();
      videoFormData.append('file', videoFile);
      const vidRes = await fetch('/upload-sector-video', { method: 'POST', body: videoFormData });
      const vidData = await vidRes.json();

      sectorsToUpload.push({
        name,
        supervisor_name: sName,
        supervisor_email: sEmail,
        video_filename: vidData.filename
      });
    }

    // Save metadata
    const setupData = new FormData();
    setupData.append('admin_id', state.user.user_id);
    setupData.append('sectors_json', JSON.stringify(sectorsToUpload));
    
    const res = await fetch('/setup-site', { method: 'POST', body: setupData });
    if (res.ok) {
      showToast('System configured successfully.');
      autoSwitchToMain();
    } else {
      throw new Error('Failed to save configuration.');
    }
  } catch (err) {
    alert(err.message);
    submitBtn.disabled = false;
    submitBtn.textContent = "Finalize Command Center";
  }
}

// 7. MONITOR LOGIC
function initMonitor() {
  if (!state.activeSector) return;
  
  document.getElementById('admin-display-name').textContent = state.user.admin_name;
  switchSector(state.activeSector.id);
  
  // Start Sampling Loop (10 seconds)
  if (state.monitorInterval) clearInterval(state.monitorInterval);
  state.monitorInterval = setInterval(captureFrame, 10000);
}

function renderSectors() {
  const list = document.getElementById('sector-list');
  list.innerHTML = '';
  state.sectors.forEach(s => {
    const btn = document.createElement('button');
    btn.className = `sector-btn w-full text-left p-4 rounded-xl group hover:bg-gold/5 transition-all mb-2 ${state.activeSector?.id === s.id ? 'active' : ''}`;
    btn.innerHTML = `
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gold/10 flex items-center justify-center italic font-display text-gold group-hover:bg-vermillion group-hover:text-cream transition-all">${s.sector_name.charAt(0)}</div>
        <div>
          <span class="block text-sm font-semibold text-cream">${s.sector_name}</span>
          <span class="text-[9px] uppercase tracking-widest text-gold/30">${s.supervisor_name}</span>
        </div>
      </div>
    `;
    btn.onclick = () => switchSector(s.id);
    list.appendChild(btn);
  });
}

function switchSector(id) {
  const sector = state.sectors.find(s => s.id === id);
  if (!sector) return;
  state.activeSector = sector;
  
  // Update UI
  document.getElementById('current-sector-title').textContent = sector.sector_name;
  document.getElementById('meta-sector-id').textContent = sector.id;
  renderSectors();

  // Update Video
  const video = document.getElementById('main-video');
  video.src = `/static/uploads/sectors/${sector.video_filename}`;
  video.play();
  
  showToast(`Switched to ${sector.sector_name}`);
}

async function captureFrame() {
  if (state.isAnalyzing || !state.activeSector) return;

  const video = document.getElementById('main-video');
  const canvas = document.getElementById('capture-canvas');
  const statusLabel = document.getElementById('monitor-status-text');

  // Capture
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0);

  statusLabel.textContent = "Analyzing Frame...";
  state.isAnalyzing = true;

  canvas.toBlob(async (blob) => {
    const formData = new FormData();
    formData.append('file', blob, `sector_${state.activeSector.id}_${Date.now()}.jpg`);
    formData.append('sector_id', state.activeSector.id);
    formData.append('admin_id', state.user.user_id);

    try {
      const res = await fetch('/process-frame', { method: 'POST', body: formData });
      const data = await res.json();

      if (data.tier === 1 && data.status === 'safe') {
        showSafePulse();
        statusLabel.textContent = "Environment Clear";
      } else if (data.status === 'processing') {
        statusLabel.textContent = "Deep Analysis Running...";
      } else if (data.status === 'complete') {
        statusLabel.textContent = "Violation Recorded";
      }
    } catch (err) {
      console.error('Frame process failed:', err);
      statusLabel.textContent = "Static Connection Error";
    } finally {
      state.isAnalyzing = false;
      setTimeout(() => {
        if (!state.isAnalyzing) statusLabel.textContent = "Real-time Stream Integration";
      }, 2000);
    }
  }, 'image/jpeg', 0.8);
}

function showSafePulse() {
  const pulse = document.getElementById('safe-pulse');
  pulse.classList.add('active');
  setTimeout(() => pulse.classList.remove('active'), 1000);
}

// 8. WEBSOCKET
function connectWebSocket() {
  if (state.ws) return;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  state.ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

  state.ws.onmessage = (e) => {
    if (e.data === 'pong') return;
    try {
      const data = JSON.parse(e.data);
      if (data.type === 'violation') handleViolation(data);
    } catch (err) {}
  };

  state.ws.onclose = () => {
    state.ws = null;
    setTimeout(connectWebSocket, 5000);
  };
}

function handleViolation(data) {
  // TTS
  if (window.speechSynthesis) {
    const utter = new SpeechSynthesisUtterance(`Alert: ${data.violation_class} in ${data.sector_name}`);
    utter.rate = 0.9;
    window.speechSynthesis.speak(utter);
  }

  // Counter
  state.violationCount++;
  document.getElementById('violation-count').textContent = state.violationCount;

  // Render Card
  const stack = document.getElementById('violation-stack');
  const empty = document.getElementById('empty-alerts');
  if (empty) empty.remove();

  const card = document.createElement('div');
  card.className = "violation-card animate-slide-in-right";
  card.innerHTML = `
    <div class="relative">
      <img src="${data.uploaded_image_url}" class="violation-image" />
      <div class="absolute top-4 left-4">
        <span class="violation-badge">${data.violation_class || 'Violation'}</span>
      </div>
      <div class="absolute top-4 right-4 text-[10px] text-white/60 bg-black/40 px-2 py-1 rounded">
        ${new Date().toLocaleTimeString()}
      </div>
    </div>
    <div class="p-6">
      <h4 class="font-display text-xl italic text-gold">${data.sector_name || 'Restricted Zone'}</h4>
      <div class="flex items-center gap-2 mt-1">
        <span class="w-1.5 h-1.5 rounded-full bg-vermillion"></span>
        <span class="text-[10px] uppercase text-gold/40 tracking-widest">Supervisor: ${data.supervisor_name || 'Node Lead'}</span>
      </div>
      
      <div class="agent-report-container">
        <span class="agent-tag">▸ Intelligence Report</span>
        <p>${data.agent_report || 'Processing agent insights...'}</p>
      </div>
    </div>
  `;
  stack.prepend(card);
  showToast(`ALARM: ${data.violation_class} in ${data.sector_name}`);
}

// 9. ANALYTICS LOGIC
async function loadAnalytics() {
  const body = document.getElementById('incidents-table-body');
  const empty = document.getElementById('analytics-empty');
  body.innerHTML = '';
  
  try {
    const res = await fetch('/incidents');
    const data = await res.json();
    
    if (data.length === 0) {
      empty.classList.remove('hidden');
      return;
    }
    
    empty.classList.add('hidden');
    data.forEach(inc => {
      const row = document.createElement('tr');
      row.className = "group transition-colors";
      row.innerHTML = `
        <td class="px-8 py-6 text-sm font-light text-gold/60">#INC-${inc.id.toString().padStart(4, '0')}</td>
        <td class="px-8 py-6">
           <span class="text-sm font-semibold text-gold">${inc.sector_name}</span>
        </td>
        <td class="px-8 py-6 text-xs text-gold/40">${new Date(inc.timestamp).toLocaleString()}</td>
        <td class="px-8 py-6">
           <span class="px-3 py-1 bg-vermillion/10 text-vermillion-light text-[10px] rounded uppercase font-bold border border-vermillion/20">${inc.violation_type}</span>
        </td>
        <td class="px-8 py-6">
           <button class="text-gold hover:text-cream text-[10px] uppercase underline tracking-widest font-bold" onclick="window.open('${inc.image_url}', '_blank')">View Evidence</button>
        </td>
        <td class="px-8 py-6">
           <span class="status-badge status-${inc.status.toLowerCase()}">${inc.status}</span>
        </td>
        <td class="px-8 py-6 text-right">
           ${inc.status === 'Pending' ? `
             <button class="text-[10px] uppercase tracking-widest font-bold px-4 py-2 border border-gold/10 rounded-lg hover:border-vermillion hover:text-vermillion transition-all" onclick="resolveIncident(${inc.id}, this)">Resolve</button>
           ` : '<span class="text-gold/20 text-[10px] uppercase font-bold">Authenticated</span>'}
        </td>
      `;
      body.appendChild(row);
    });
  } catch (err) {}
}

async function resolveIncident(id, btn) {
  btn.disabled = true;
  btn.textContent = "...";
  try {
    const res = await fetch(`/incidents/${id}/resolve`, { method: 'POST' });
    if (res.ok) {
      showToast(`Incident #${id} resolved.`);
      loadAnalytics();
    }
  } catch (err) {}
}

// 10. HELPERS
function startClock() {
  const clock = document.getElementById('live-clock');
  const dateEl = document.getElementById('live-date');
  function tick() {
    const now = new Date();
    clock.textContent = now.toLocaleTimeString('en-GB');
    dateEl.textContent = now.toLocaleDateString('en-GB', { weekday: 'long', day: '2-digit', month: 'long' });
  }
  setInterval(tick, 1000); tick();
}

async function checkSystemHealth() {
  const label = document.getElementById('colab-status-text');
  try {
    const res = await fetch('/health');
    const data = await res.json();
    label.textContent = data.colab_connected ? "Neural Engine Live" : "Colab Disconnected";
    label.classList.toggle('text-vermillion', !data.colab_connected);
  } catch (err) {
    label.textContent = "Local Server Offline";
  }
}

function showToast(msg) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = "px-6 py-4 rounded-xl shadow-2xl border border-gold/20 bg-ink-medium text-cream text-sm font-semibold animate-slide-in-right flex items-center gap-3";
  toast.innerHTML = `
    <div class="w-2 h-2 rounded-full bg-vermillion"></div>
    <span>${msg}</span>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.5s';
    setTimeout(() => toast.remove(), 500);
  }, 4000);
}