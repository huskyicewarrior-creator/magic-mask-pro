const $ = (id) => document.getElementById(id);
const statusEl = $('status');
let projectId = null;
let currentVideoId = null;
let timelineClips = [];
let selectedBackground = 'greenscreen';
const maskState = { points_add: [], points_remove: [] };

async function api(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function setStatus(text, error = false) {
  statusEl.textContent = text;
  statusEl.style.color = error ? '#ff7878' : '#9e9e9e';
}

$('installSam2Btn').onclick = async () => {
  const report = await api('/api/install/sam2', { method: 'POST' });
  setStatus(report.ok ? 'SAM2 setup complete.' : 'SAM2 setup reported warnings.', !report.ok);
};

$('createProjectBtn').onclick = async () => {
  const fd = new FormData();
  fd.append('name', $('projectName').value || 'Untitled Project');
  const data = await api('/api/projects', { method: 'POST', body: fd });
  projectId = data.project_id;
  setStatus(`Project created: ${data.name}`);
};

$('videoUpload').onchange = async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  const data = await api('/api/upload-video', { method: 'POST', body: fd });
  currentVideoId = data.video_id;
  $('previewVideo').src = `/api/media/${data.video_id}`;
  $('clipInfo').textContent = `Loaded ${data.video_id} (${data.duration}s)`;
  setStatus('Video loaded');
};

$('addClipBtn').onclick = async () => {
  if (!projectId || !currentVideoId) return setStatus('Create project and upload video first', true);
  const clip = await api('/api/clips', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      video_id: currentVideoId,
      in_point: Number($('trimStart').value || 0),
      out_point: Number($('trimEnd').value || 0) || null,
    }),
  });
  timelineClips.push(clip);
  renderTimeline();
};

$('previewVideo').addEventListener('click', (e) => {
  const video = $('previewVideo');
  const rect = video.getBoundingClientRect();
  const x = Math.round(((e.clientX - rect.left) / rect.width) * video.videoWidth);
  const y = Math.round(((e.clientY - rect.top) / rect.height) * video.videoHeight);
  if (!x || !y) return;
  if (e.shiftKey) maskState.points_remove.push({ x, y });
  else maskState.points_add.push({ x, y });
  setStatus(`${e.shiftKey ? 'Erase' : 'Add'} point (${x}, ${y})`);
});

$('previewMaskBtn').onclick = async () => {
  if (!currentVideoId || !maskState.points_add.length) return setStatus('Add at least one positive point.', true);
  const video = $('previewVideo');
  const fd = new FormData();
  fd.append('video_id', currentVideoId);
  fd.append('point_x', maskState.points_add[0].x);
  fd.append('point_y', maskState.points_add[0].y);
  fd.append('time_s', video.currentTime || 0);
  fd.append('config_json', JSON.stringify({ ...maskState, dilation_px: +$('dilationPx').value, feather_px: +$('featherPx').value }));
  const data = await api('/api/preview-mask', { method: 'POST', body: fd });
  const img = new Image();
  img.onload = () => {
    const canvas = $('overlayCanvas');
    canvas.width = img.width;
    canvas.height = img.height;
    canvas.getContext('2d').drawImage(img, 0, 0);
  };
  img.src = `data:image/png;base64,${data.overlay}`;
};

$('clearMaskBtn').onclick = () => {
  maskState.points_add = [];
  maskState.points_remove = [];
  const c = $('overlayCanvas');
  c.getContext('2d').clearRect(0, 0, c.width, c.height);
};

async function loadBackgrounds() {
  const items = await api('/api/backgrounds');
  $('backgroundGrid').innerHTML = '';
  items.forEach((bg) => {
    const el = document.createElement('div');
    el.className = `bg-tile ${selectedBackground === bg.id ? 'active' : ''}`;
    el.innerHTML = `<img src="${bg.url}"/><small>${bg.name}</small>`;
    el.onclick = () => {
      selectedBackground = bg.id;
      loadBackgrounds();
    };
    $('backgroundGrid').appendChild(el);
  });
}

$('backgroundUpload').onchange = async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  const res = await api('/api/upload-background', { method: 'POST', body: fd });
  selectedBackground = res.background_id;
  loadBackgrounds();
};

function renderTimeline() {
  const track = $('timelineTrack');
  track.innerHTML = '';
  timelineClips.sort((a, b) => a.position - b.position).forEach((clip) => {
    const block = document.createElement('div');
    block.className = 'clip-block';
    block.draggable = true;
    block.dataset.id = clip.clip_id;
    block.innerHTML = `<strong>${clip.clip_id}</strong><br/>In: ${clip.in_point}s`;
    block.addEventListener('dragstart', (e) => e.dataTransfer.setData('text/plain', clip.clip_id));
    track.appendChild(block);
  });
}

$('timelineTrack').addEventListener('dragover', (e) => e.preventDefault());
$('timelineTrack').addEventListener('drop', async (e) => {
  e.preventDefault();
  const id = e.dataTransfer.getData('text/plain');
  const idx = [...$('timelineTrack').children].findIndex((child) => e.clientX < child.getBoundingClientRect().right);
  const current = timelineClips.find((c) => c.clip_id === id);
  if (!current) return;
  timelineClips = timelineClips.filter((c) => c.clip_id !== id);
  timelineClips.splice(idx < 0 ? timelineClips.length : idx, 0, current);
  timelineClips.forEach((c, i) => (c.position = i));
  renderTimeline();
  if (projectId) {
    await api(`/api/projects/${projectId}/timeline-order`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clip_ids: timelineClips.map((c) => c.clip_id) }),
    });
  }
});

$('exportBtn').onclick = async () => {
  if (!projectId || !currentVideoId || !maskState.points_add.length) return setStatus('Need project, video and mask point', true);
  const payload = {
    project_id: projectId,
    background_id: selectedBackground,
    video_id: currentVideoId,
    trim_start: +$('trimStart').value || 0,
    trim_end: +$('trimEnd').value || null,
    disable_trim: $('disableTrim').checked,
    mask: { ...maskState, dilation_px: +$('dilationPx').value, feather_px: +$('featherPx').value },
  };
  const { job_id } = await api('/api/export', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const poll = setInterval(async () => {
    const job = await api(`/api/jobs/${job_id}`);
    $('progressBar').style.width = `${job.progress || 0}%`;
    if (job.status === 'done' || job.status === 'error') {
      clearInterval(poll);
      setStatus(job.status === 'done' ? 'Export complete' : (job.error || 'Export failed'), job.status === 'error');
      if (job.status === 'done') loadLibrary();
    }
  }, 800);
};

async function loadLibrary() {
  const items = await api('/api/library');
  $('library').innerHTML = '';
  items.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `<video controls src="${item.url}"></video><a href="${item.url}" download>Download export</a>`;
    $('library').appendChild(card);
  });
}

loadBackgrounds();
loadLibrary();
