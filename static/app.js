
// FRONTEND LOGIC — Connects the browser to the local FastAPI server.
//   1. WebSocket client with auto-reconnect + health indicator
//   2. Image upload via drag-and-drop / file picker
//   3. Browser TTS for instant vocal alerts on violation
//   4. Violation card creation with slide-in animation
//   5. Card minimize/expand toggle + vertical stacking

// 1. GLOBAL STATE

let ws = null;                       // WebSocket connection instance
let violationCount = 0;              // Running count of violations detected
let reconnectAttempts = 0;           // Track reconnect tries for backoff
const MAX_RECONNECT_DELAY = 10000;   // Max wait between reconnects (10s)


// 2. WEBSOCKET — Real-time connection to FastAPI backend

/** Create and manage the WebSocket connection with auto-reconnect. */
function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${window.location.host}/ws`);

  ws.onopen = () => {
    reconnectAttempts = 0;
    updateConnectionStatus(true);
    // Heartbeat ping every 25s to prevent server-side timeout
    ws._pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 25000);
  };

  ws.onmessage = (event) => {
    // Ignore pong responses from our heartbeat
    if (event.data === "pong") return;

    try {
      const data = JSON.parse(event.data);
      if (data.type === "violation") handleViolationEvent(data);
      if (data.type === "email_sent") handleEmailSentEvent();
    } catch (err) {
      console.warn("[WS] Failed to parse message:", err);
    }
  };

  ws.onclose = () => {
    clearInterval(ws._pingInterval);
    updateConnectionStatus(false);
    // Reconnect with exponential backoff
    const delay = Math.min(1000 * 2 ** reconnectAttempts, MAX_RECONNECT_DELAY);
    reconnectAttempts++;
    setTimeout(connectWebSocket, delay);
  };

  ws.onerror = () => ws.close();
}

/** Update the green/red dot + text in the header bar. */
function updateConnectionStatus(connected) {
  const dot = document.getElementById("ws-dot");
  const label = document.getElementById("ws-status");
  dot.className = `w-2.5 h-2.5 rounded-full ${connected ? "dot-connected" : "dot-disconnected"}`;
  label.textContent = connected ? "Live" : "Reconnecting…";
}


// 3. HEALTH CHECK — Periodic check for Colab connectivity

/** Poll the /health endpoint every 15s to show Colab status in the header. */
async function checkSystemHealth() {
  try {
    const res = await fetch("/health");
    const data = await res.json();

    const dot = document.getElementById("colab-dot");
    const label = document.getElementById("colab-status");

    if (data.colab_connected) {
      dot.className = "w-2.5 h-2.5 rounded-full dot-connected";
      label.textContent = "Colab: Connected";
    } else {
      dot.className = "w-2.5 h-2.5 rounded-full dot-disconnected";
      label.textContent = "Colab: Offline";
    }
  } catch {
    // Server itself is down
    document.getElementById("colab-status").textContent = "Colab: Unknown";
  }
}


// 4. IMAGE UPLOAD — Drag-and-drop + file picker

function setupUpload() {
  const zone = document.getElementById("upload-zone");
  const input = document.getElementById("file-input");

  // Drag-and-drop visual feedback
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));

  // Handle dropped files
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("drag-over");
    const file = e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) uploadImage(file);
  });

  // Handle file picker selection
  input.addEventListener("change", () => {
    if (input.files[0]) uploadImage(input.files[0]);
  });
}

/** Send the selected image to the backend for AI processing. */
async function uploadImage(file) {
  // Show loading state
  document.getElementById("upload-zone").classList.add("hidden");
  const indicator = document.getElementById("processing-indicator");
  indicator.classList.remove("hidden");
  document.getElementById("processing-text").textContent = "Uploading image…";

  const formData = new FormData();
  formData.append("file", file);
  
  const userName = localStorage.getItem("userName") || "Unknown User";
  const userEmail = localStorage.getItem("userEmail") || "unknown@example.com";
  formData.append("user_name", userName);
  formData.append("user_email", userEmail);

  try {
    document.getElementById("processing-text").textContent = "Analyzing image with YOLO…";

    const response = await fetch("/upload-image", { method: "POST", body: formData });
    const result = await response.json();

    if (result.error) {
      // Show error and reset upload zone
      alert(`Error: ${result.error}\n${result.hint || ""}`);
      resetUploadZone();
      return;
    }

    // If Colab returned results synchronously, the WebSocket handler
    // already created the card. Show the uploaded image.
    if (result.status === "complete" && result.data) {
      showDetectionView(result.data);
    } else if (result.status === "processing") {
      // Colab is still working — poll for status
      document.getElementById("processing-text").textContent = "AI pipeline running…";
      pollJobStatus(result.job_id, result.image_url);
    }

  } catch (err) {
    alert("Upload failed. Is the server running?");
    console.error(err);
    resetUploadZone();
  }
}

/** Poll Colab job status every 2s (fallback if WebSocket push fails). */
function pollJobStatus(jobId, imageUrl) {
  const poll = setInterval(async () => {
    try {
      const res = await fetch(`/job-status/${jobId}`);
      const status = await res.json();
      if (status.status === "complete") {
        clearInterval(poll);
        showDetectionView(status.data || status);
      } else if (status.status === "error") {
        clearInterval(poll);
        alert("Processing failed: " + (status.message || "Unknown error"));
        resetUploadZone();
      }
    } catch {
      clearInterval(poll);
      resetUploadZone();
    }
  }, 2000);
}

/** Display the annotated image with bounding boxes. */
function showDetectionView(data) {
  document.getElementById("processing-indicator").classList.add("hidden");
  const view = document.getElementById("detection-view");
  view.classList.remove("hidden");

  // Show the annotated image (base64 from Colab or local URL)
  const img = document.getElementById("detected-image");
  if (data.annotated_image_base64) {
    img.src = `data:image/jpeg;base64,${data.annotated_image_base64}`;
  } else if (data.uploaded_image_url) {
    img.src = data.uploaded_image_url;
  }

  // Show detection summary text
  const summary = document.getElementById("detection-summary");
  const classes = data.violation_class || data.violations_found || "Analysis complete";
  summary.textContent = typeof classes === "string" ? classes : classes.join(" · ");
}

/** Reset the upload zone so user can upload another image. */
function resetUploadZone() {
  document.getElementById("processing-indicator").classList.add("hidden");
  document.getElementById("upload-zone").classList.remove("hidden");
  document.getElementById("file-input").value = "";
}


// 5. VIOLATION EVENT HANDLER — TTS + Card creation

/** Called when a violation event arrives via WebSocket. */
function handleViolationEvent(data) {
  // Play browser TTS vocal alert
  speakAlert(data.violation_class);

  // Show detection view if not already visible
  showDetectionView(data);

  // Create the violation card in the sidebar
  createViolationCard(data);

  // Update violation counter in header
  violationCount++;
  document.getElementById("violation-count").textContent = violationCount;

  // Remove the empty-state placeholder
  const empty = document.getElementById("empty-state");
  if (empty) empty.remove();
}

/** Speak a violation alert using the browser's built-in TTS engine. */
function speakAlert(violationClass) {
  if (!window.speechSynthesis) return;

  // Convert class name to natural language (e.g. "no-vest" → "No Vest")
  const label = (violationClass || "violation")
    .split(/[_-]/)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  const utterance = new SpeechSynthesisUtterance(`Violation Detected! ${label}`);
  utterance.rate = 1.0;
  utterance.pitch = 0.9;
  utterance.volume = 1.0;
  window.speechSynthesis.speak(utterance);
}


// 6. VIOLATION CARD — Build, animate, minimize

/** Create a rich violation card with all pipeline output sections. */
function createViolationCard(data) {
  const card = document.createElement("div");
  card.className = "violation-card slide-in";
  const cardId = `card-${Date.now()}`;
  card.id = cardId;

  // Determine the badge class for color-coding
  const badgeClass = (data.violation_class || "").includes("helmet") ? "no-helmet" : "no-vest";
  const timeStr = data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();

  //  Build card HTML 
  card.innerHTML = `
    <!-- HEADER: violation type badge + timestamp + minimize button -->
    <div class="card-header" onclick="toggleCard('${cardId}')">
      <span class="badge ${badgeClass}">${data.violation_class || "Violation"}</span>
      <span class="timestamp">${timeStr}</span>
      <button class="minimize-btn" onclick="event.stopPropagation(); minimizeCard('${cardId}')" title="Minimize">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"/></svg>
      </button>
    </div>

    <!-- BODY: all pipeline results stacked vertically -->
    <div class="card-body" id="body-${cardId}">
      <!-- Input image snapshot -->
      ${buildSnapshotSection(data)}

      <!-- Similar Incident History (Image-to-Image retrieval) -->
      ${buildSimilarSection(data)}

      <!-- RAG output snippet (OSHA legal references) -->
      ${buildRagSection(data)}

      <!-- Agent Report (CrewAI Safety Auditor + Legal Critic output) -->
      ${buildAgentSection(data)}
    </div>
  `;

  // Insert at the top of the cards container
  const container = document.getElementById("cards-container");
  container.prepend(card);
}

/** Snapshot image at the top of each card. */
function buildSnapshotSection(data) {
  let src = "";
  if (data.snapshot_base64) src = `data:image/jpeg;base64,${data.snapshot_base64}`;
  else if (data.uploaded_image_url) src = data.uploaded_image_url;
  else if (data.annotated_image_base64) src = `data:image/jpeg;base64,${data.annotated_image_base64}`;
  if (!src) return "";
  return `<img src="${src}" alt="Violation snapshot" loading="lazy" />`;
}

/** Image-to-image similar incidents grid. */
function buildSimilarSection(data) {
  const score = data.similarity_score ?? data.similarity ?? null;
  const images = data.similar_images || [];

  if (score === null && images.length === 0) return "";

  let html = `<div class="card-section-title">Similar Incident History</div>`;

  // Similarity score bar
  if (score !== null) {
    const pct = Math.min(Math.round(score * 100), 100);
    html += `
      <div style="display:flex;justify-content:space-between;font-size:0.75rem;color:rgba(223,188,148,0.6);margin-bottom:4px">
        <span>Similarity</span><span>${typeof score === "number" ? score.toFixed(3) : score}</span>
      </div>
      <div class="score-bar"><div class="score-bar-fill" style="width:${pct}%"></div></div>
    `;
  }

  // Grid of similar images (up to 6)
  if (images.length > 0) {
    html += `<div class="similar-grid" style="margin-top:10px">`;
    images.slice(0, 6).forEach((img) => {
      const imgSrc = img.startsWith("data:") ? img : `data:image/jpeg;base64,${img}`;
      html += `<img src="${imgSrc}" alt="Similar incident" loading="lazy" />`;
    });
    html += `</div>`;
  }

  return html;
}

/** RAG output snippet (OSHA clause + penalties). */
function buildRagSection(data) {
  if (!data.rag_snippet) return "";
  return `
    <div class="card-section-title">OSHA Legal Reference</div>
    <div class="rag-snippet">${escapeHtml(data.rag_snippet)}</div>
  `;
}

/** Agent Chain report (Safety Auditor + Legal Critic). */
function buildAgentSection(data) {
  if (!data.agent_report) return "";
  return `
    <div class="card-section-title">Agent Report</div>
    <div class="agent-console">
      <span class="agent-1">▸ Safety Auditor → Legal Critic</span><br/>
      <pre style="white-space:pre-wrap;margin:6px 0 0">${escapeHtml(data.agent_report)}</pre>
    </div>
  `;
}


// 7. CARD INTERACTIONS — Toggle body & minimize to stack

/** Expand or collapse the card body on header click. */
function toggleCard(cardId) {
  const body = document.getElementById(`body-${cardId}`);
  if (!body) return;
  body.style.display = body.style.display === "none" ? "block" : "none";
}

/** Move the card to the minimized stack at the bottom. */
function minimizeCard(cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;

  // Extract badge text for the minimized label
  const badge = card.querySelector(".badge");
  const time = card.querySelector(".timestamp");
  const label = badge ? badge.textContent : "Violation";
  const timeText = time ? time.textContent : "";

  // Hide the full card
  card.style.display = "none";

  // Create a minimized entry in the bottom stack
  const mini = document.createElement("div");
  mini.className = "minimized-card";
  mini.innerHTML = `
    <span class="badge ${badge?.classList[1] || "no-vest"}" style="font-size:0.65rem;padding:2px 6px">${label}</span>
    <span class="timestamp">${timeText}</span>
  `;

  // Click on minimized card to restore it
  mini.addEventListener("click", () => {
    card.style.display = "block";
    mini.remove();
  });

  document.getElementById("minimized-stack").prepend(mini);
}


// 8. UTILITY FUNCTIONS

/** Prevent XSS by escaping HTML entities in dynamic content. */
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

/** Live clock in the header — updates every second. */
function startClock() {
  const clockEl = document.getElementById("live-clock");
  const dateEl = document.getElementById("live-date");

  function tick() {
    const now = new Date();
    clockEl.textContent = now.toLocaleTimeString("en-US", { hour12: false });
    dateEl.textContent = now.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  }
  tick();
  setInterval(tick, 1000);
}


// 9. ONBOARDING & TOAST NOTIFICATIONS

/** Show onboarding modal if user details aren't stored */
function setupOnboarding() {
  const modal = document.getElementById("user-modal");
  const inputName = document.getElementById("user-name-input");
  const inputEmail = document.getElementById("user-email-input");
  const submitBtn = document.getElementById("modal-submit-btn");
  const errorMsg = document.getElementById("modal-error");

  const storedName = localStorage.getItem("userName");
  const storedEmail = localStorage.getItem("userEmail");

  if (!storedName || !storedEmail) {
    modal.classList.remove("hidden");
  }

  submitBtn.addEventListener("click", () => {
    const name = inputName.value.trim();
    const email = inputEmail.value.trim();
    
    if (name && email) {
      localStorage.setItem("userName", name);
      localStorage.setItem("userEmail", email);
      modal.classList.add("hidden");
      errorMsg.classList.add("hidden");
    } else {
      errorMsg.classList.remove("hidden");
    }
  });
}

/** Show a toast notification when N8N finishes sending the email */
function handleEmailSentEvent() {
  const container = document.getElementById("toast-container");
  
  const toast = document.createElement("div");
  toast.className = "bg-green-500/10 border border-green-500/50 text-green-400 px-4 py-3 rounded-xl shadow-lg flex items-center gap-3 slide-in backdrop-blur-md";
  toast.innerHTML = `
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
    <div class="flex flex-col">
      <span class="text-sm font-semibold text-cream">The email has been sent</span>
      <span class="text-xs opacity-80">Compliance report dispatched securely.</span>
    </div>
  `;
  
  container.appendChild(toast);
  
  // Remove after 5 seconds
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(20px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 5000);
}


// 10. INITIALIZATION — Run when the page loads

document.addEventListener("DOMContentLoaded", () => {
  setupOnboarding();        // Ask for user info if missing
  connectWebSocket();       // Open real-time connection to backend
  setupUpload();            // Attach drag-and-drop + file picker handlers
  startClock();             // Start the live header clock
  checkSystemHealth();      // Initial health check
  setInterval(checkSystemHealth, 15000); // Re-check every 15 seconds

  // Clear all cards button
  document.getElementById("clear-cards-btn").addEventListener("click", () => {
    document.getElementById("cards-container").innerHTML = `
      <div id="empty-state" class="flex flex-col items-center justify-center h-full text-center opacity-60">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#DFBC94" stroke-width="1" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
        <p class="mt-4 text-gold/50 text-sm">No violations detected yet.<br/>Upload an image to begin scanning.</p>
      </div>
    `;
    document.getElementById("minimized-stack").innerHTML = "";
    violationCount = 0;
    document.getElementById("violation-count").textContent = "0";
    // Show upload zone again
    resetUploadZone();
    document.getElementById("detection-view").classList.add("hidden");
  });
});