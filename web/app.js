const videoUpload = document.getElementById('videoUpload');
const previewVideo = document.getElementById('previewVideo');
const maskCanvas = document.getElementById('maskCanvas');
const overlayBtn = document.getElementById('overlayBtn');
const previewTrimBtn = document.getElementById('previewTrimBtn');
const processBtn = document.getElementById('processBtn');
const backgroundList = document.getElementById('backgroundList');
const backgroundUpload = document.getElementById('backgroundUpload');
const progressBar = document.getElementById('progressBar');
const statusText = document.getElementById('statusText');
const libraryEl = document.getElementById('library');
const trimStart = document.getElementById('trimStart');
const trimEnd = document.getElementById('trimEnd');
const disableTrim = document.getElementById('disableTrim');
const trimControls = document.getElementById('trimControls');
const trimInfo = document.getElementById('trimInfo');

let selectedPoint = null;
let videoId = null;
let selectedBackground = 'greenscreen';
let trimPreviewActive = false;

function setStatus(msg, isError = false) {
  statusText.textContent = msg;
  statusText.className = isError ? 'error' : '';
}

async function apiJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data.detail) detail = data.detail;
    } catch {
      // keep status text
    }
    throw new Error(detail);
  }
  return res.json();
}

async function loadBackgrounds() {
  try {
    const data = await apiJson('/api/backgrounds');
    backgroundList.innerHTML = '';
    data.forEach((bg) => {
      const tile = document.createElement('div');
      tile.className = `bg-tile ${bg.id === selectedBackground ? 'active' : ''}`;
      tile.innerHTML = `<img src="${bg.url}" alt="${bg.name}"/><small>${bg.name}</small>`;
      tile.onclick = () => {
        selectedBackground = bg.id;
        loadBackgrounds();
      };
      backgroundList.appendChild(tile);
    });
  } catch (err) {
    setStatus(`Could not load backgrounds: ${err.message}`, true);
  }
}

function updateTrimUI() {
  const isDisabled = disableTrim.checked;
  trimControls.classList.toggle('disabled', isDisabled);
  if (!previewVideo.duration || Number.isNaN(previewVideo.duration)) return;
  const start = Number(trimStart.value || 0);
  const end = Number(trimEnd.value || 0);
  if (isDisabled) {
    trimInfo.textContent = `No trim enabled — full duration ${previewVideo.duration.toFixed(2)}s`;
  } else {
    trimInfo.textContent = `Trim preview window: ${start.toFixed(2)}s → ${Math.max(end, start).toFixed(2)}s`;
  }
}

videoUpload.addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  try {
    const form = new FormData();
    form.append('file', file);
    const data = await apiJson('/api/upload-video', { method: 'POST', body: form });
    videoId = data.video_id;
    previewVideo.src = URL.createObjectURL(file);
    setStatus('Video uploaded. Click object to track.');
  } catch (err) {
    setStatus(`Upload failed: ${err.message}`, true);
  }
});

previewVideo.addEventListener('loadedmetadata', () => {
  trimEnd.value = previewVideo.duration.toFixed(2);
  updateTrimUI();
});

[trimStart, trimEnd, disableTrim].forEach((el) => el.addEventListener('input', updateTrimUI));

previewTrimBtn.addEventListener('click', async () => {
  if (!previewVideo.duration) return;
  if (disableTrim.checked) {
    previewVideo.currentTime = 0;
    await previewVideo.play();
    return;
  }
  const start = Math.max(0, Number(trimStart.value || 0));
  const end = Math.min(previewVideo.duration, Math.max(start + 0.1, Number(trimEnd.value || 0)));
  previewVideo.currentTime = start;
  trimPreviewActive = true;
  await previewVideo.play();
  const handler = () => {
    if (trimPreviewActive && previewVideo.currentTime >= end) {
      previewVideo.pause();
      trimPreviewActive = false;
      previewVideo.removeEventListener('timeupdate', handler);
    }
  };
  previewVideo.addEventListener('timeupdate', handler);
});

previewVideo.addEventListener('click', (e) => {
  if (!previewVideo.videoWidth || !previewVideo.videoHeight) return;
  const r = previewVideo.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * previewVideo.videoWidth;
  const y = ((e.clientY - r.top) / r.height) * previewVideo.videoHeight;
  selectedPoint = { x: Math.round(x), y: Math.round(y) };
  setStatus(`Selected object point: (${selectedPoint.x}, ${selectedPoint.y})`);
});

overlayBtn.addEventListener('click', async () => {
  if (!videoId || !selectedPoint) return setStatus('Upload video and click object first.', true);
  try {
    const form = new FormData();
    form.append('video_id', videoId);
    form.append('point_x', selectedPoint.x);
    form.append('point_y', selectedPoint.y);
    form.append('time_s', previewVideo.currentTime || 0);
    const data = await apiJson('/api/preview-mask', { method: 'POST', body: form });
    const img = new Image();
    img.onload = () => {
      maskCanvas.width = img.width;
      maskCanvas.height = img.height;
      maskCanvas.getContext('2d').drawImage(img, 0, 0);
    };
    img.src = `data:image/png;base64,${data.overlay}`;
  } catch (err) {
    setStatus(`Overlay failed: ${err.message}`, true);
  }
});

backgroundUpload.addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  try {
    const form = new FormData();
    form.append('file', file);
    const data = await apiJson('/api/upload-background', { method: 'POST', body: form });
    selectedBackground = data.background_id;
    await loadBackgrounds();
  } catch (err) {
    setStatus(`Background upload failed: ${err.message}`, true);
  }
});

processBtn.addEventListener('click', async () => {
  if (!videoId || !selectedPoint) return setStatus('Missing video or mask point.', true);
  try {
    const start = Number(trimStart.value || 0);
    const end = Number(trimEnd.value || 0);
    const req = {
      video_id: videoId,
      background_id: selectedBackground,
      point_x: selectedPoint.x,
      point_y: selectedPoint.y,
      trim_start: start,
      trim_end: end > start ? end : null,
      disable_trim: disableTrim.checked,
    };

    const { job_id } = await apiJson('/api/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });

    setStatus('Processing…');
    const poll = setInterval(async () => {
      try {
        const job = await apiJson(`/api/jobs/${job_id}`);
        progressBar.style.width = `${job.progress || 0}%`;
        if (job.status === 'done') {
          setStatus('Done! Added to library.');
          clearInterval(poll);
          await loadLibrary();
        }
        if (job.status === 'error') {
          setStatus(`Error: ${job.error}`, true);
          clearInterval(poll);
        }
      } catch (err) {
        setStatus(`Polling failed: ${err.message}`, true);
        clearInterval(poll);
      }
    }, 900);
  } catch (err) {
    setStatus(`Process start failed: ${err.message}`, true);
  }
});

async function loadLibrary() {
  try {
    const items = await apiJson('/api/library');
    libraryEl.innerHTML = '';
    items.forEach((item) => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <video controls src="${item.url}" style="width:100%;border-radius:8px"></video>
        <a href="${item.url}" download>Download MP4</a>
        <div>${new Date(item.created_at).toLocaleString()}</div>
      `;
      libraryEl.appendChild(card);
    });
  } catch (err) {
    setStatus(`Library load failed: ${err.message}`, true);
  }
}

loadBackgrounds();
loadLibrary();
updateTrimUI();
