/* ═══════════════════════════════════════════════════════════
   PPTX-Slides — Web Application Frontend
   Developed by ChimSe (viduvan) - https://github.com/viduvan
   Completed: February 27, 2026
   ═══════════════════════════════════════════════════════════
   Handles file upload, slide generation, preview, editing, and download.
 */

const API_BASE = window.location.origin;

// ── State ──────────────────────────────────────────────────
const state = {
    sessionId: null,
    slides: [],
    wordContent: '',
    isLoading: false,
    selectedTheme: 'auto',
};

// ── DOM Elements ───────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
    // Upload
    uploadZone: $('#uploadZone'),
    fileInput: $('#fileInput'),
    uploadStatus: $('#uploadStatus'),
    fileName: $('#fileName'),
    removeFile: $('#removeFile'),
    uploadInfo: $('#uploadInfo'),

    // Prompt & Actions
    promptInput: $('#promptInput'),
    generateBtn: $('#generateBtn'),

    // Main Content
    emptyState: $('#emptyState'),
    loadingState: $('#loadingState'),
    loadingText: $('#loadingText'),
    slidesArea: $('#slidesArea'),
    slideCount: $('#slideCount'),
    slidesGrid: $('#slidesGrid'),

    // Edit
    editInput: $('#editInput'),
    editBtn: $('#editBtn'),
    undoBtn: $('#undoBtn'),
    downloadBtn: $('#downloadBtn'),

    // Modal
    slideModal: $('#slideModal'),
    modalTitle: $('#modalTitle'),
    modalSlideTitle: $('#modalSlideTitle'),
    modalSlideContent: $('#modalSlideContent'),
    modalSlideNarration: $('#modalSlideNarration'),
    modalNarrationField: $('#modalNarrationField'),
    modalClose: $('#modalClose'),

    // Status
    statusBadge: $('#statusBadge'),
    statusText: $('#statusText'),
    statusDot: $('.status-dot'),

    // Toast
    toastContainer: $('#toastContainer'),

    // Theme
    themeSelector: $('#themeSelector'),
};

// ── Initialization ─────────────────────────────────────────
function init() {
    initI18n();
    setupUpload();
    setupGenerate();
    setupEdit();
    setupModal();
    setupKeyboard();
    loadThemes();
}

// ── Upload Handling ────────────────────────────────────────
function setupUpload() {
    // Click to upload
    dom.uploadZone.addEventListener('click', () => dom.fileInput.click());

    // File input change
    dom.fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) uploadFile(e.target.files[0]);
    });

    // Drag & Drop
    dom.uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dom.uploadZone.classList.add('drag-over');
    });

    dom.uploadZone.addEventListener('dragleave', () => {
        dom.uploadZone.classList.remove('drag-over');
    });

    dom.uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dom.uploadZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) uploadFile(e.dataTransfer.files[0]);
    });

    // Remove file
    dom.removeFile.addEventListener('click', (e) => {
        e.stopPropagation();
        state.wordContent = '';
        dom.uploadZone.hidden = false;
        dom.uploadStatus.hidden = true;
        dom.fileInput.value = '';
    });
}

async function uploadFile(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['docx', 'pdf'].includes(ext)) {
        showToast(t('toast.file.invalid'), 'error');
        return;
    }

    setStatus(t('status.uploading'), 'loading');
    dom.fileName.textContent = file.name;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch(`${API_BASE}/api/upload/document`, {
            method: 'POST',
            body: formData,
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Upload failed');
        }

        const data = await res.json();
        state.wordContent = data.word_content;

        dom.uploadZone.hidden = true;
        dom.uploadStatus.hidden = false;
        dom.uploadInfo.textContent = `${data.word_count} ${t('upload.words')}${data.was_summarized ? ' ' + t('upload.summarized') : ''}`;

        setStatus(t('status.ready'), 'ready');
        showToast(`${t('toast.upload.success')} ${file.name}`, 'success');

    } catch (err) {
        setStatus(t('status.upload.failed'), 'error');
        showToast(`${t('toast.upload.error')} ${err.message}`, 'error');
        setTimeout(() => setStatus(t('status.ready'), 'ready'), 3000);
    }
}

// ── Generate Slides ────────────────────────────────────────
function setupGenerate() {
    dom.generateBtn.addEventListener('click', generateSlides);

    // Ctrl+Enter to generate
    dom.promptInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            generateSlides();
        }
    });
}

async function generateSlides() {
    const prompt = dom.promptInput.value.trim();
    if (!prompt) {
        showToast(t('toast.prompt.empty'), 'error');
        dom.promptInput.focus();
        return;
    }

    showLoading(t('loading.generating'));

    try {
        const res = await fetch(`${API_BASE}/api/slides/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                prompt,
                word_content: state.wordContent,
                theme: state.selectedTheme,
            }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Generation failed');
        }

        const data = await res.json();
        state.sessionId = data.session_id;
        state.slides = data.slides;

        renderSlides();
        hideLoading();
        setStatus(t('status.ready'), 'ready');
        showToast(data.message, 'success');

    } catch (err) {
        hideLoading();
        setStatus(t('status.error'), 'error');
        showToast(`${t('toast.gen.error')} ${err.message}`, 'error');
        setTimeout(() => setStatus(t('status.ready'), 'ready'), 3000);
    }
}

// ── Edit Slides ────────────────────────────────────────────
function setupEdit() {
    dom.editBtn.addEventListener('click', editSlides);
    dom.undoBtn.addEventListener('click', undoSlides);
    dom.downloadBtn.addEventListener('click', downloadSlides);

    // Ctrl+Enter to edit
    dom.editInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            editSlides();
        }
    });
}

async function editSlides() {
    const prompt = dom.editInput.value.trim();
    if (!prompt) {
        showToast(t('toast.edit.empty'), 'error');
        dom.editInput.focus();
        return;
    }
    if (!state.sessionId) {
        showToast(t('toast.edit.no.session'), 'error');
        return;
    }

    showLoading(t('loading.editing'));

    try {
        const res = await fetch(`${API_BASE}/api/slides/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: state.sessionId,
                prompt,
            }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Edit failed');
        }

        const data = await res.json();
        state.slides = data.slides;

        dom.editInput.value = '';
        renderSlides();
        hideLoading();
        setStatus(t('status.ready'), 'ready');
        showToast(data.message, 'success');

    } catch (err) {
        hideLoading();
        setStatus(t('status.error'), 'error');
        showToast(`${t('toast.edit.error')} ${err.message}`, 'error');
        setTimeout(() => setStatus(t('status.ready'), 'ready'), 3000);
    }
}

async function undoSlides() {
    if (!state.sessionId) return;

    setStatus(t('status.undoing'), 'loading');

    try {
        const res = await fetch(`${API_BASE}/api/slides/${state.sessionId}/undo`, {
            method: 'POST',
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Undo failed');
        }

        const data = await res.json();
        state.slides = data.slides;

        renderSlides();
        setStatus(t('status.ready'), 'ready');
        showToast(t('toast.undo.success'), 'info');

    } catch (err) {
        setStatus(t('status.error'), 'error');
        showToast(`${t('toast.undo.error')} ${err.message}`, 'error');
        setTimeout(() => setStatus(t('status.ready'), 'ready'), 3000);
    }
}

async function downloadSlides() {
    if (!state.sessionId) return;

    setStatus(t('status.downloading'), 'loading');

    try {
        const res = await fetch(`${API_BASE}/api/slides/${state.sessionId}/download`);

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Download failed');
        }

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'slides_presentation_VietPV.pptx';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        setStatus(t('status.ready'), 'ready');
        showToast(t('toast.download.success'), 'success');

    } catch (err) {
        setStatus(t('status.error'), 'error');
        showToast(`${t('toast.download.error')} ${err.message}`, 'error');
        setTimeout(() => setStatus(t('status.ready'), 'ready'), 3000);
    }
}

// ── Render Slides ──────────────────────────────────────────
function renderSlides() {
    dom.emptyState.hidden = true;
    dom.loadingState.hidden = true;
    dom.slidesArea.hidden = false;
    dom.slideCount.textContent = state.slides.length;

    dom.slidesGrid.innerHTML = '';

    state.slides.forEach((slide, index) => {
        const card = document.createElement('div');
        card.className = 'slide-card';
        card.style.animationDelay = `${index * 60}ms`;

        card.innerHTML = `
            <div class="slide-card__header">
                <span class="slide-card__number">${t('slide.label')} ${slide.slide_number}</span>
                <div class="slide-card__actions">
                    <button class="btn-icon" title="${t('slide.view')}">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                            <circle cx="12" cy="12" r="3"/>
                        </svg>
                    </button>
                </div>
            </div>
            <div class="slide-card__body">
                <div class="slide-card__title">${escapeHtml(slide.title)}</div>
                <div class="slide-card__content">${escapeHtml(slide.content)}</div>
            </div>
        `;

        card.addEventListener('click', () => openSlideModal(slide));
        dom.slidesGrid.appendChild(card);
    });
}

// ── Modal ──────────────────────────────────────────────────
function setupModal() {
    dom.modalClose.addEventListener('click', closeModal);
    dom.slideModal.addEventListener('click', (e) => {
        if (e.target === dom.slideModal) closeModal();
    });
}

function openSlideModal(slide) {
    dom.modalTitle.textContent = `${t('slide.label')} ${slide.slide_number}`;
    dom.modalSlideTitle.textContent = slide.title || t('modal.no.title');
    dom.modalSlideContent.textContent = slide.content || t('modal.no.content');

    if (slide.narration && slide.narration.trim()) {
        dom.modalNarrationField.hidden = false;
        dom.modalSlideNarration.textContent = slide.narration;
    } else {
        dom.modalNarrationField.hidden = true;
    }

    dom.slideModal.hidden = false;
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    dom.slideModal.hidden = true;
    document.body.style.overflow = '';
}

// ── Keyboard Shortcuts ─────────────────────────────────────
function setupKeyboard() {
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (!dom.slideModal.hidden) closeModal();
        }
    });
}

// ── UI Helpers ─────────────────────────────────────────────
function showLoading(text) {
    state.isLoading = true;
    dom.emptyState.hidden = true;
    dom.slidesArea.hidden = true;
    dom.loadingState.hidden = false;
    dom.loadingText.textContent = text;
    dom.generateBtn.disabled = true;
    dom.editBtn.disabled = true;
    setStatus(text, 'loading');
}

function hideLoading() {
    state.isLoading = false;
    dom.loadingState.hidden = true;
    dom.generateBtn.disabled = false;
    dom.editBtn.disabled = false;

    if (state.slides.length > 0) {
        dom.slidesArea.hidden = false;
    } else {
        dom.emptyState.hidden = false;
    }
}

function setStatus(text, type = 'ready') {
    dom.statusText.textContent = text;
    dom.statusDot.className = 'status-dot';
    if (type === 'loading') dom.statusDot.classList.add('loading');
    if (type === 'error') dom.statusDot.classList.add('error');
}

function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    dom.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('hiding');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ── Theme Selector ─────────────────────────────────────────
async function loadThemes() {
    try {
        const res = await fetch(`${API_BASE}/api/slides/themes`);
        if (!res.ok) return;

        const data = await res.json();
        const container = dom.themeSelector;

        // Build category tabs
        const tabBar = document.createElement('div');
        tabBar.className = 'theme-tabs';

        const tabContent = document.createElement('div');
        tabContent.className = 'theme-tabs__content';

        data.categories.forEach((cat, idx) => {
            // Tab button
            const tab = document.createElement('button');
            tab.className = 'theme-tab' + (idx === 0 ? ' active' : '');
            tab.dataset.category = cat.id;
            const label = currentLang === 'vi' ? (cat.label_vi || cat.label) : cat.label;
            tab.innerHTML = `<span>${cat.emoji}</span> <span class="theme-tab__label">${label}</span>`;
            tab.addEventListener('click', () => {
                tabBar.querySelectorAll('.theme-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                tabContent.querySelectorAll('.theme-category').forEach(p => p.hidden = true);
                tabContent.querySelector(`[data-cat="${cat.id}"]`).hidden = false;
            });
            tabBar.appendChild(tab);

            // Theme grid for this category
            const panel = document.createElement('div');
            panel.className = 'theme-category';
            panel.dataset.cat = cat.id;
            panel.hidden = idx !== 0;

            cat.themes.forEach(theme => {
                const btn = document.createElement('button');
                btn.className = 'theme-option';
                btn.dataset.theme = theme.id;
                const themeLabel = currentLang === 'vi' ? (theme.label_vi || theme.label) : theme.label;
                btn.title = themeLabel;
                btn.innerHTML = `
                    <span class="theme-option__color" style="background: linear-gradient(135deg, ${theme.accent}, ${theme.bg});">${theme.emoji}</span>
                    <span class="theme-option__label">${themeLabel}</span>
                `;
                panel.appendChild(btn);
            });

            tabContent.appendChild(panel);
        });

        container.appendChild(tabBar);
        container.appendChild(tabContent);

        // Click handlers for all theme buttons
        container.addEventListener('click', (e) => {
            const btn = e.target.closest('.theme-option');
            if (!btn) return;
            selectTheme(btn.dataset.theme);
        });

    } catch (err) {
        console.warn('Failed to load themes:', err);
    }
}

function selectTheme(themeId) {
    state.selectedTheme = themeId;
    // Update active state across all categories
    dom.themeSelector.querySelectorAll('.theme-option').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.theme === themeId);
    });
}

// ── Start ──────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', init);
