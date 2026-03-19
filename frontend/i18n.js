/* ═══════════════════════════════════════════════════════════
   PPTX-Slides — Internationalization (i18n)
   Supports: English (en), Vietnamese (vi)
   ═══════════════════════════════════════════════════════════ */

const I18N_STORAGE_KEY = 'pptx-lang';

const translations = {
    en: {
        // ── Navbar ──
        'nav.title': 'PPTX-Slides (VietPV)',
        'status.ready': 'Ready',

        // ── Sidebar headings ──
        'sidebar.upload': 'Upload Document',
        'sidebar.prompt': 'Prompt',
        'sidebar.theme': 'Theme',
        'theme.category.all': 'All',

        // ── Upload zone ──
        'upload.dragdrop': 'Drag & drop',
        'upload.dragdrop.filetypes': '.docx or .pdf here',
        'upload.hint': 'or click to browse',
        'upload.remove.title': 'Remove file',

        // ── Theme ──
        'theme.auto': 'Auto',
        'theme.auto.title': 'Auto — detect from content',

        // ── Buttons ──
        'btn.generate': 'Generate Slides',
        'btn.undo': 'Undo',
        'btn.download': 'Download PPTX',
        'btn.edit': 'Edit Slides',

        // ── Empty state ──
        'empty.title': 'Create Your Presentation',
        'empty.desc': 'Upload a document or write a prompt to get started.',
        'empty.desc2': 'AI will generate beautiful slides for you.',

        // ── Loading ──
        'loading.generating': 'Generating slides with AI...',
        'loading.editing': 'Editing slides with AI...',

        // ── Slides area ──
        'slides.count.suffix': 'Slides',
        'slide.label': 'Slide',
        'slide.view': 'View details',

        // ── Modal ──
        'modal.label.title': 'Title',
        'modal.label.content': 'Content',
        'modal.label.narration': 'Narration / Notes',
        'modal.no.title': '(No title)',
        'modal.no.content': '(No content)',

        // ── Prompt placeholders ──
        'placeholder.prompt': 'Describe your presentation...\n\nE.g.: Create 5 slides about the future of AI in healthcare, including an intro, key technologies, challenges, case studies, and conclusion.',
        'placeholder.edit': 'Describe what you want to change... E.g.: Add more details to slide 3, remove slide 5, add a new slide about market trends after slide 2',

        // ── Status messages ──
        'status.uploading': 'Uploading...',
        'status.upload.failed': 'Upload failed',
        'status.error': 'Error',
        'status.undoing': 'Undoing...',
        'status.downloading': 'Preparing download...',

        // ── Toast messages ──
        'toast.file.invalid': 'Only .docx and .pdf files are supported',
        'toast.upload.success': 'Document uploaded:',
        'toast.upload.error': 'Upload error:',
        'toast.prompt.empty': 'Please enter a prompt first',
        'toast.gen.error': 'Error:',
        'toast.edit.empty': 'Please describe what to change',
        'toast.edit.no.session': 'No active session. Generate slides first.',
        'toast.edit.error': 'Error:',
        'toast.undo.success': 'Reverted to previous version',
        'toast.undo.error': 'Error:',
        'toast.download.success': 'Presentation downloaded!',
        'toast.download.error': 'Download error:',
        'upload.words': 'words',
        'upload.summarized': '(summarized)',

        // ── Theme Preview ──
        'preview.heading': 'Theme Preview',
        'preview.close': 'Close',
        'preview.category': 'Category',
        'preview.select': 'Use This Theme',
        'preview.slide.title': 'Title Slide',
        'preview.slide.content': 'Content Slide',
        'preview.slide.ending': 'Ending Slide',
    },

    vi: {
        // ── Navbar ──
        'nav.title': 'PPTX-Slides (VietPV)',
        'status.ready': 'Sẵn sàng',

        // ── Sidebar headings ──
        'sidebar.upload': 'Tải tài liệu',
        'sidebar.prompt': 'Yêu cầu',
        'sidebar.theme': 'Giao diện',
        'theme.category.all': 'Tất cả',

        // ── Upload zone ──
        'upload.dragdrop': 'Kéo & thả',
        'upload.dragdrop.filetypes': '.docx hoặc .pdf vào đây',
        'upload.hint': 'hoặc nhấn để chọn tệp',
        'upload.remove.title': 'Xoá tệp',

        // ── Theme ──
        'theme.auto': 'Tự động',
        'theme.auto.title': 'Tự động — phát hiện từ nội dung',

        // ── Buttons ──
        'btn.generate': 'Tạo Slides',
        'btn.undo': 'Hoàn tác',
        'btn.download': 'Tải PPTX',
        'btn.edit': 'Chỉnh sửa Slides',

        // ── Empty state ──
        'empty.title': 'Tạo bài thuyết trình',
        'empty.desc': 'Tải tài liệu hoặc nhập yêu cầu để bắt đầu.',
        'empty.desc2': 'AI sẽ tạo slide đẹp cho bạn.',

        // ── Loading ──
        'loading.generating': 'Đang tạo slides bằng AI...',
        'loading.editing': 'Đang chỉnh sửa slides bằng AI...',

        // ── Slides area ──
        'slides.count.suffix': 'Slides',
        'slide.label': 'Slide',
        'slide.view': 'Xem chi tiết',

        // ── Modal ──
        'modal.label.title': 'Tiêu đề',
        'modal.label.content': 'Nội dung',
        'modal.label.narration': 'Ghi chú / Lời dẫn',
        'modal.no.title': '(Không có tiêu đề)',
        'modal.no.content': '(Không có nội dung)',

        // ── Prompt placeholders ──
        'placeholder.prompt': 'Mô tả bài thuyết trình của bạn...\n\nVí dụ: Tạo 5 slide về tương lai của AI trong y tế, bao gồm giới thiệu, công nghệ chính, thách thức, nghiên cứu điển hình và kết luận.',
        'placeholder.edit': 'Mô tả những gì bạn muốn thay đổi... Ví dụ: Thêm chi tiết vào slide 3, xoá slide 5, thêm slide mới về xu hướng thị trường sau slide 2',

        // ── Status messages ──
        'status.uploading': 'Đang tải lên...',
        'status.upload.failed': 'Tải lên thất bại',
        'status.error': 'Lỗi',
        'status.undoing': 'Đang hoàn tác...',
        'status.downloading': 'Đang chuẩn bị tải xuống...',

        // ── Toast messages ──
        'toast.file.invalid': 'Chỉ hỗ trợ tệp .docx và .pdf',
        'toast.upload.success': 'Đã tải tài liệu:',
        'toast.upload.error': 'Lỗi tải lên:',
        'toast.prompt.empty': 'Vui lòng nhập yêu cầu trước',
        'toast.gen.error': 'Lỗi:',
        'toast.edit.empty': 'Vui lòng mô tả những gì cần thay đổi',
        'toast.edit.no.session': 'Chưa có phiên làm việc. Hãy tạo slides trước.',
        'toast.edit.error': 'Lỗi:',
        'toast.undo.success': 'Đã quay lại phiên bản trước',
        'toast.undo.error': 'Lỗi:',
        'toast.download.success': 'Đã tải bài thuyết trình!',
        'toast.download.error': 'Lỗi tải xuống:',
        'upload.words': 'từ',
        'upload.summarized': '(đã tóm tắt)',

        // ── Theme Preview ──
        'preview.heading': 'Xem trước giao diện',
        'preview.close': 'Đóng',
        'preview.category': 'Danh mục',
        'preview.select': 'Dùng giao diện này',
        'preview.slide.title': 'Slide tiêu đề',
        'preview.slide.content': 'Slide nội dung',
        'preview.slide.ending': 'Slide kết thúc',
    },
};

let currentLang = 'en';

/**
 * Get translation for a key in the current language.
 */
function t(key) {
    return (translations[currentLang] && translations[currentLang][key]) || key;
}

/**
 * Apply translations to all elements with [data-i18n] and [data-i18n-placeholder].
 */
function applyTranslations() {
    // Text content
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        el.textContent = t(key);
    });

    // Placeholders
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        el.placeholder = t(key);
    });

    // Title attributes
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        el.title = t(key);
    });

    // Update html lang attribute
    document.documentElement.lang = currentLang === 'vi' ? 'vi' : 'en';
}

/**
 * Set the active language and persist.
 */
function setLanguage(lang) {
    if (!translations[lang]) return;
    currentLang = lang;
    localStorage.setItem(I18N_STORAGE_KEY, lang);
    applyTranslations();

    // Update active button
    document.querySelectorAll('.lang-switcher__btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === lang);
    });
}

/**
 * Initialize i18n: load saved language and set up switcher.
 */
function initI18n() {
    const saved = localStorage.getItem(I18N_STORAGE_KEY);
    currentLang = saved && translations[saved] ? saved : 'en';
    applyTranslations();

    // Mark active button
    document.querySelectorAll('.lang-switcher__btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === currentLang);
        btn.addEventListener('click', () => setLanguage(btn.dataset.lang));
    });
}
