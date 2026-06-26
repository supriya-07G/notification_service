// static/template-editor.js

const SAMPLE_DATA = {
    customer_name: 'Jane Doe',
    appointment_date: 'Tuesday, June 20',
    appointment_time: '10:00 AM',
    appointment_type: 'Service',
    location: '123 Green Street, Boston, MA',
    calendar_source: 'HVAC'
};

let currentQuill = null;
let currentTemplateId = null;
let currentChannel = 'sms';
let currentRawBody = '';

// ── Edit Template ────────────────────────────────────────────────────────────

function openTemplateEditor(templateId) {
    currentTemplateId = templateId;
    document.getElementById('templates-list-view').classList.add('d-none');
    document.getElementById('template-editor-view').classList.remove('d-none');

    fetch(`/dashboard/api/templates/${templateId}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) { alert('Error loading template: ' + data.error); closeTemplateEditor(); return; }

            currentChannel = data.channel;
            currentRawBody = data.body || '';

            document.getElementById('editor-badge-channel').textContent = data.channel.toUpperCase();
            document.getElementById('editor-badge-rule').textContent = data.rule_name;
            document.getElementById('editor-badge-lang').textContent = data.language.toUpperCase();

            // Subject line (email only)
            const subjectContainer = document.getElementById('subject-container');
            if (data.channel === 'email') {
                subjectContainer.classList.remove('d-none');
                document.getElementById('template-subject').value = data.subject || '';
            } else {
                subjectContainer.classList.add('d-none');
            }

            if (data.channel === 'email') {
                // Email: use raw textarea, hide Quill
                document.getElementById('editor').style.display = 'none';
                let ta = document.getElementById('email-raw-editor');
                if (!ta) {
                    ta = document.createElement('textarea');
                    ta.id = 'email-raw-editor';
                    ta.className = 'form-control font-monospace small';
                    ta.style.cssText = 'height:300px;resize:vertical;';
                    document.getElementById('editor').insertAdjacentElement('afterend', ta);
                    ta.addEventListener('input', () => { currentRawBody = ta.value; debounceUpdatePreview(); });
                }
                ta.style.display = '';
                ta.value = currentRawBody;
            } else {
                // SMS: use Quill
                document.getElementById('editor').style.display = '';
                const ta = document.getElementById('email-raw-editor');
                if (ta) ta.style.display = 'none';

                if (!currentQuill) {
                    currentQuill = new Quill('#editor', {
                        theme: 'snow',
                        modules: { toolbar: [['bold','italic','underline'],[{'list':'ordered'},{'list':'bullet'}],['link','image'],['clean']] }
                    });
                    currentQuill.on('text-change', debounceUpdatePreview);
                }
                currentQuill.clipboard.dangerouslyPasteHTML(currentRawBody);
            }

            updatePreview();
        })
        .catch(err => { console.error(err); alert('Failed to load template.'); closeTemplateEditor(); });
}

function closeTemplateEditor() {
    currentTemplateId = null;
    document.getElementById('templates-list-view').classList.remove('d-none');
    document.getElementById('template-editor-view').classList.add('d-none');
}

function insertPlaceholder(placeholder) {
    const token = `{{${placeholder}}}`;
    if (currentChannel === 'email') {
        const ta = document.getElementById('email-raw-editor');
        if (!ta) return;
        const s = ta.selectionStart;
        ta.value = ta.value.slice(0, s) + token + ta.value.slice(ta.selectionEnd);
        ta.selectionStart = ta.selectionEnd = s + token.length;
        currentRawBody = ta.value;
        debounceUpdatePreview();
        return;
    }
    if (!currentQuill) return;
    const range = currentQuill.getSelection(true);
    currentQuill.insertText(range.index, token);
    currentQuill.setSelection(range.index + token.length);
}

function insertPlaceholderRaw(text) {
    const ta = document.getElementById('email-raw-editor');
    if (!ta) return;
    const s = ta.selectionStart;
    ta.value = ta.value.slice(0, s) + text + ta.value.slice(ta.selectionEnd);
    ta.selectionStart = ta.selectionEnd = s + text.length;
    currentRawBody = ta.value;
    debounceUpdatePreview();
}

let previewTimeout = null;
function debounceUpdatePreview() {
    clearTimeout(previewTimeout);
    previewTimeout = setTimeout(updatePreview, 400);
}

function updatePreview() {
    const previewEl = document.getElementById('preview-content');
    const counterEl = document.getElementById('char-counter');
    if (!previewEl) return;

    if (currentChannel === 'email') {
        let html = currentRawBody;
        for (const [k, v] of Object.entries(SAMPLE_DATA)) {
            html = html.replace(new RegExp(`{{${k}}}`, 'g'), v);
        }
        html = html.replace(/\[LOGO_BANNER\]/g,
            `<div style="text-align:center;padding:16px 0 8px;background:#ffffff;">` +
            `<img src="/dashboard/logo_partner.png" alt="EcoSave" style="max-width:220px;height:auto;">` +
            `</div><hr style="border:none;border-top:2px solid #2e7d32;margin:0 0 16px;">`
        );
        previewEl.style.cssText = 'padding:0;white-space:normal;';
        previewEl.innerHTML = '<iframe id="email-preview-frame" style="width:100%;height:420px;border:none;border-radius:4px;" sandbox="allow-same-origin"></iframe>';
        document.getElementById('email-preview-frame').srcdoc = html;
        if (counterEl) counterEl.innerHTML = '<i>Email — no length limit.</i>';
        return;
    }

    if (!currentQuill) return;

    let plainText = currentQuill.getText().replace(/\n$/, '');
    let previewText = plainText;
    for (const [k, v] of Object.entries(SAMPLE_DATA)) {
        previewText = previewText.replace(new RegExp(`{{${k}}}`, 'g'), v);
    }

    previewEl.style.cssText = 'white-space:pre-wrap;word-wrap:break-word;padding:0.75rem;';
    previewEl.textContent = previewText || '(Start typing to see preview)';

    if (counterEl) {
        const stats = countSmsCharacters(plainText);
        counterEl.innerHTML = stats.message + (stats.warning ? `<br><span class="text-danger">${stats.warning}</span>` : '');
    }
}

function countSmsCharacters(text) {
    const count = text.length;
    const segments = Math.ceil(count / 160) || 1;
    const message = `${count} characters (${segments} segment${segments > 1 ? 's' : ''})`;
    const warning = count > 160 ? `⚠️ ${segments} segments — extra charge applies`
                  : count > 155 ? `⚠️ Approaching 1-segment limit` : '';
    return { count, segments, message, warning };
}

function translateTemplate() {
    if (!currentTemplateId) return;
    const body = currentChannel === 'email'
        ? (document.getElementById('email-raw-editor') || {}).value || ''
        : (currentQuill ? currentQuill.root.innerHTML : '');
    const btn = document.getElementById('btn-translate');
    btn.innerHTML = 'Translating...'; btn.disabled = true;
    fetch(`/dashboard/api/templates/${currentTemplateId}/translate`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ body })
    }).then(r => r.json()).then(data => {
        if (data.translated_body) {
            if (currentChannel === 'email') {
                const ta = document.getElementById('email-raw-editor');
                if (ta) { ta.value = data.translated_body; currentRawBody = ta.value; updatePreview(); }
            } else {
                currentQuill.clipboard.dangerouslyPasteHTML(data.translated_body);
            }
        } else if (data.error) alert('Error: ' + data.error);
    }).finally(() => { btn.innerHTML = '<i class="bi bi-translate"></i> Auto-Translate'; btn.disabled = false; });
}

function revertTemplate() {
    if (!currentTemplateId) return;
    if (!confirm('Revert to system default? Changes will be lost until you save.')) return;
    fetch(`/dashboard/api/templates/${currentTemplateId}/revert`, { method: 'POST', headers: {'Content-Type':'application/json'} })
        .then(r => r.json())
        .then(data => {
            if (data.default_body) {
                currentRawBody = data.default_body;
                if (currentChannel === 'email') {
                    const ta = document.getElementById('email-raw-editor');
                    if (ta) ta.value = currentRawBody;
                } else {
                    currentQuill.clipboard.dangerouslyPasteHTML(currentRawBody);
                }
                updatePreview();
            }
        });
}

function saveTemplate() {
    if (!currentTemplateId) return;
    const ta = document.getElementById('email-raw-editor');
    const bodyHtml = (currentChannel === 'email' && ta) ? ta.value : (currentQuill ? currentQuill.root.innerHTML : '');
    const subject = document.getElementById('template-subject').value;
    const btn = document.getElementById('btn-save');
    btn.innerHTML = 'Saving...'; btn.disabled = true;
    fetch(`/dashboard/api/templates/${currentTemplateId}`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ body: bodyHtml, subject })
    }).then(r => r.json()).then(data => {
        if (data.success) { alert('Template saved successfully!'); window.location.reload(); }
        else { alert('Failed to save: ' + data.error); btn.innerHTML = 'Save Template'; btn.disabled = false; }
    });
}

function sendTest() {
    if (!currentTemplateId) return;
    const recipient = prompt(`Enter a ${currentChannel === 'sms' ? 'phone number' : 'email address'} for the test:`);
    if (!recipient) return;
    const btn = document.getElementById('btn-test-send');
    btn.innerHTML = 'Sending...'; btn.disabled = true;
    fetch(`/dashboard/api/templates/${currentTemplateId}/test-send`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ to: recipient })
    }).then(r => r.json()).then(data => {
        if (data.success) alert(data.message);
        else alert('Test send failed: ' + data.error);
    }).finally(() => { btn.innerHTML = '<i class="bi bi-send-fill"></i> Send Test to Me'; btn.disabled = false; });
}

// ── Add New Template ─────────────────────────────────────────────────────────

let newQuill = null;

document.addEventListener('DOMContentLoaded', () => {
    const newEditorEl = document.getElementById('new_editor');
    if (!newEditorEl) return;

    newQuill = new Quill('#new_editor', {
        theme: 'snow',
        modules: { toolbar: [['bold','italic','underline'],[{'list':'ordered'},{'list':'bullet'}],['link','image'],['clean']] }
    });

    newQuill.on('text-change', () => {
        const html = newQuill.root.innerHTML;
        document.getElementById('new_body_hidden').value = html === '<p><br></p>' ? '' : html;
        updateNewPreview();
    });

    const languageSelect = document.getElementById('new_language');
    if (languageSelect) {
        const defaults = {
            'en': "Hi {{customer_name}}, this is a reminder for your {{appointment_type}} appointment on {{appointment_date}} at {{appointment_time}}.",
            'pt': "Olá {{customer_name}}, este é um lembrete para a sua marcação de {{appointment_type}} em {{appointment_date}} às {{appointment_time}}.",
            'pt-br': "Olá {{customer_name}}, este é um lembrete do seu agendamento de {{appointment_type}} para o dia {{appointment_date}} às {{appointment_time}}.",
            'es': "Hola {{customer_name}}, este es un recordatorio para su cita de {{appointment_type}} el {{appointment_date}} a las {{appointment_time}}."
        };
        languageSelect.addEventListener('change', e => {
            const cur = newQuill.getText().trim();
            const isDefault = !cur || Object.values(defaults).some(t => cur.startsWith(t.substring(0, 20)));
            if (isDefault) { newQuill.setText(''); newQuill.insertText(0, defaults[e.target.value] || defaults['en']); }
        });
        if (!newQuill.getText().trim()) newQuill.insertText(0, defaults['en']);
    }

    document.querySelector('#addTemplateCard select[name="channel"]')
        ?.addEventListener('change', updateNewPreview);
});

function insertNewPlaceholder(placeholder) {
    if (!newQuill) return;
    const range = newQuill.getSelection(true);
    const token = `{{${placeholder}}}`;
    newQuill.insertText(range.index, token);
    newQuill.setSelection(range.index + token.length);
}

function insertNewPlaceholderRaw(text) {
    if (!newQuill) return;
    const range = newQuill.getSelection(true);
    newQuill.insertText(range.index, text);
    newQuill.setSelection(range.index + text.length);
}

function updateNewPreview() {
    if (!newQuill) return;
    const channelEl = document.querySelector('#addTemplateCard select[name="channel"]');
    const channel = channelEl ? channelEl.value.toLowerCase() : 'sms';
    const previewEl = document.getElementById('new_preview');
    if (!previewEl) return;

    const lengthSpan = document.getElementById('new_preview_len');
    const warningSpan = document.getElementById('new_preview_sms_warning');
    const statsContainer = lengthSpan ? lengthSpan.closest('.d-flex') : null;

    if (channel === 'email') {
        let html = newQuill.root.innerHTML;
        for (const [k, v] of Object.entries(SAMPLE_DATA)) html = html.replace(new RegExp(`{{${k}}}`, 'g'), v);
        previewEl.style.cssText = 'padding:0;white-space:normal;min-height:48px;';
        previewEl.innerHTML = `<iframe srcdoc="${html.replace(/"/g, '&quot;')}" style="width:100%;height:300px;border:none;border-radius:4px;" sandbox="allow-same-origin"></iframe>`;
        if (statsContainer) statsContainer.classList.add('d-none');
        return;
    }

    let plainText = newQuill.getText().replace(/\n$/, '');
    let previewText = plainText;
    for (const [k, v] of Object.entries(SAMPLE_DATA)) previewText = previewText.replace(new RegExp(`{{${k}}}`, 'g'), v);

    previewEl.style.cssText = 'white-space:pre-wrap;word-wrap:break-word;padding:0.5rem;min-height:48px;';
    previewEl.textContent = previewText || '(Type message body to preview)';

    if (statsContainer) statsContainer.classList.remove('d-none');
    const stats = countSmsCharacters(plainText);
    if (lengthSpan) lengthSpan.textContent = stats.count;
    if (warningSpan) warningSpan.classList.toggle('d-none', stats.count <= 160);
}

function translateNewTemplate(languageSelectId) {
    if (!newQuill) return;
    const html = newQuill.root.innerHTML;
    if (!html || html === '<p><br></p>') return;
    const select = document.getElementById(languageSelectId);
    const langMap = {'en':'en','pt':'pt-PT','pt-br':'pt','es':'es'};
    const targetLang = langMap[select ? select.value : 'pt'] || 'pt';
    if (targetLang === 'en') { alert('Already in English.'); return; }
    const btn = document.getElementById('btn-translate-new');
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Translating...'; btn.disabled = true;
    fetch(`/dashboard/api/templates/0/translate`, {
        method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ body: html })
    }).then(r => r.json()).then(data => {
        if (data.translated_body) newQuill.clipboard.dangerouslyPasteHTML(data.translated_body);
        else if (data.error) alert('Error: ' + data.error);
    }).finally(() => { btn.innerHTML = '<i class="bi bi-translate"></i> Auto-Translate to Selected Language'; btn.disabled = false; });
}
