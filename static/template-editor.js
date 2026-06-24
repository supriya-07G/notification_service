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

// Initialize the editor UI
function openTemplateEditor(templateId) {
    currentTemplateId = templateId;
    
    // Hide the template table and show the editor UI
    document.getElementById('templates-list-view').classList.add('d-none');
    document.getElementById('template-editor-view').classList.remove('d-none');
    
    // Fetch template data
    fetch(`/dashboard/api/templates/${templateId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                alert('Error loading template: ' + data.error);
                closeTemplateEditor();
                return;
            }
            
            currentChannel = data.channel;
            
            // Populate Info Bar
            document.getElementById('editor-badge-channel').textContent = data.channel.toUpperCase();
            document.getElementById('editor-badge-rule').textContent = data.rule_name;
            document.getElementById('editor-badge-lang').textContent = data.language.toUpperCase();
            
            // Handle Subject
            const subjectContainer = document.getElementById('subject-container');
            if (data.channel === 'email') {
                subjectContainer.classList.remove('d-none');
                document.getElementById('template-subject').value = data.subject || '';
            } else {
                subjectContainer.classList.add('d-none');
                document.getElementById('template-subject').value = '';
            }
            
            // Initialize or clear Quill
            if (!currentQuill) {
                currentQuill = new Quill('#editor', {
                    theme: 'snow',
                    modules: {
                        toolbar: [
                            ['bold', 'italic', 'underline'],
                            [{ 'list': 'ordered'}, { 'list': 'bullet' }],
                            ['link', 'image'],
                            ['clean']
                        ]
                    }
                });
                
                currentQuill.on('text-change', function() {
                    debounceUpdatePreview();
                });
            }
            
            // Set content
            // We use clipboard.dangerouslyPasteHTML to preserve formatting
            currentQuill.clipboard.dangerouslyPasteHTML(data.body || '');
            
            updatePreview();
        })
        .catch(err => {
            console.error(err);
            alert('Failed to load template.');
            closeTemplateEditor();
        });
}

function closeTemplateEditor() {
    currentTemplateId = null;
    document.getElementById('templates-list-view').classList.remove('d-none');
    document.getElementById('template-editor-view').classList.add('d-none');
}

function insertPlaceholder(placeholder) {
    if (!currentQuill) return;
    const range = currentQuill.getSelection(true);
    currentQuill.insertText(range.index, `{{${placeholder}}}`);
    currentQuill.setSelection(range.index + placeholder.length + 4);
}

let previewTimeout = null;
function debounceUpdatePreview() {
    clearTimeout(previewTimeout);
    previewTimeout = setTimeout(updatePreview, 500);
}

function updatePreview() {
    if (!currentQuill) return;
    
    // Get raw HTML
    let content = currentQuill.root.innerHTML;
    
    // Check SMS length based on plain text
    let plainText = currentQuill.getText().replace(/\n$/, '');
    
    // Replace placeholders with SAMPLE_DATA in BOTH content and plainText
    for (const [key, value] of Object.entries(SAMPLE_DATA)) {
        const regex = new RegExp(`{{${key}}}`, 'g');
        content = content.replace(regex, `<span class="bg-warning bg-opacity-25">${value}</span>`);
        plainText = plainText.replace(regex, value);
    }
    
    document.getElementById('preview-content').innerHTML = content;
    
    // Update Character Counter
    if (currentChannel === 'sms') {
        const stats = countSmsCharacters(plainText);
        const counterEl = document.getElementById('char-counter');
        counterEl.innerHTML = stats.message;
        if (stats.warning) {
            counterEl.innerHTML += `<br><span class="text-danger">${stats.warning}</span>`;
        }
    } else {
        document.getElementById('char-counter').innerHTML = '<i>Email length is practically unlimited.</i>';
    }
}

function countSmsCharacters(text) {
    const count = text.length;
    const segments = Math.ceil(count / 160) || 1;
    
    let message = `${count} characters`;
    if (segments > 1) {
        message += ` (${segments} segments)`;
    } else {
        message += ` (1 segment)`;
    }
    
    let warning = '';
    if (count > 160) {
        warning = `⚠️ ${segments} segments — extra charge applies`;
    } else if (count > 155) {
        warning = `⚠️ Approaching 1-segment limit (160 chars)`;
    }
    
    return { count, segments, message, warning };
}

function translateTemplate() {
    if (!currentQuill || !currentTemplateId) return;
    
    const currentHtml = currentQuill.root.innerHTML;
    const btn = document.getElementById('btn-translate');
    const originalText = btn.innerHTML;
    btn.innerHTML = 'Translating...';
    btn.disabled = true;
    
    fetch(`/dashboard/api/templates/${currentTemplateId}/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: currentHtml })
    })
    .then(res => res.json())
    .then(data => {
        if (data.translated_body) {
            currentQuill.clipboard.dangerouslyPasteHTML(data.translated_body);
        } else if (data.error) {
            alert('Error: ' + data.error);
        }
    })
    .finally(() => {
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}

function revertTemplate() {
    if (!currentQuill || !currentTemplateId) return;
    
    if (!confirm("Are you sure you want to revert this template to the system default? This cannot be undone until you save.")) {
        return;
    }
    
    fetch(`/dashboard/api/templates/${currentTemplateId}/revert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    })
    .then(res => res.json())
    .then(data => {
        if (data.default_body) {
            currentQuill.clipboard.dangerouslyPasteHTML(data.default_body);
        }
    });
}

function saveTemplate() {
    if (!currentQuill || !currentTemplateId) return;
    
    const bodyHtml = currentQuill.root.innerHTML;
    const subject = document.getElementById('template-subject').value;
    
    const btn = document.getElementById('btn-save');
    const originalText = btn.innerHTML;
    btn.innerHTML = 'Saving...';
    btn.disabled = true;
    
    fetch(`/dashboard/api/templates/${currentTemplateId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: bodyHtml, subject: subject })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            alert("Template saved successfully!");
            // Update the preview in the main table if needed, or just reload
            window.location.reload();
        } else {
            alert("Failed to save: " + data.error);
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    });
}

function sendTest() {
    if (!currentTemplateId) return;
    
    const recipient = prompt(`Enter a ${currentChannel === 'sms' ? 'phone number' : 'email address'} to receive the test message:`);
    if (!recipient) return;
    
    const btn = document.getElementById('btn-test-send');
    const originalText = btn.innerHTML;
    btn.innerHTML = 'Sending...';
    btn.disabled = true;
    
    fetch(`/dashboard/api/templates/${currentTemplateId}/test-send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to: recipient })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            alert(data.message);
        } else {
            alert("Test send failed: " + data.error);
        }
    })
    .finally(() => {
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}

// --- Add New Template Editor Logic ---
let newQuill = null;

document.addEventListener('DOMContentLoaded', () => {
    const newEditorEl = document.getElementById('new_editor');
    if (newEditorEl) {
        newQuill = new Quill('#new_editor', {
            theme: 'snow',
            modules: {
                toolbar: [
                    ['bold', 'italic', 'underline'],
                    [{ 'list': 'ordered'}, { 'list': 'bullet' }],
                    ['link', 'image'],
                    ['clean']
                ]
            }
        });

        // Sync Quill content to hidden input and update preview
        newQuill.on('text-change', function() {
            const html = newQuill.root.innerHTML;
            document.getElementById('new_body_hidden').value = html === '<p><br></p>' ? '' : html;
            updateNewPreview();
        });
    }
    
    // Handle language change auto-population
    const languageSelect = document.getElementById('new_language');
    if (languageSelect && newQuill) {
        languageSelect.addEventListener('change', (e) => {
            const lang = e.target.value;
            const templates = {
                'en': "Hi {{customer_name}}, this is a reminder for your {{appointment_type}} appointment on {{appointment_date}} at {{appointment_time}}.",
                'pt': "Olá {{customer_name}}, este é um lembrete para a sua marcação de {{appointment_type}} em {{appointment_date}} às {{appointment_time}}.",
                'pt-br': "Olá {{customer_name}}, este é um lembrete do seu agendamento de {{appointment_type}} para o dia {{appointment_date}} às {{appointment_time}}.",
                'es': "Hola {{customer_name}}, este es un recordatorio para su cita de {{appointment_type}} el {{appointment_date}} a las {{appointment_time}}."
            };
            
            const currentText = newQuill.getText().trim();
            const defaultTexts = Object.values(templates).map(t => t.replace(/<[^>]*>?/gm, '').trim());
            
            // Only replace if empty or matches one of the defaults
            if (!currentText || defaultTexts.some(t => currentText.includes(t.substring(0, 20)))) {
               newQuill.setText(''); // clear formatting
               newQuill.insertText(0, templates[lang] || templates['en']);
            }
        });
        
        // Populate initial content if empty
        if (!newQuill.getText().trim()) {
           newQuill.insertText(0, "Hi {{customer_name}}, this is a reminder for your {{appointment_type}} appointment on {{appointment_date}} at {{appointment_time}}.");
        }
    }
    
    // Listen to channel change to update preview format
    const newChannel = document.querySelector('#addTemplateCard select[name="channel"]');
    if (newChannel) {
        newChannel.addEventListener('change', () => {
            updateNewPreview();
        });
    }
});

function insertNewPlaceholder(placeholder) {
    if (!newQuill) return;
    const range = newQuill.getSelection(true);
    newQuill.insertText(range.index, `{{${placeholder}}}`);
    newQuill.setSelection(range.index + placeholder.length + 4);
}

function updateNewPreview() {
    if (!newQuill) return;
    
    let content = newQuill.root.innerHTML;
    let plainText = newQuill.getText().replace(/\n$/, '');
    
    for (const [key, value] of Object.entries(SAMPLE_DATA)) {
        const regex = new RegExp(`{{${key}}}`, 'g');
        content = content.replace(regex, `<span class="bg-warning bg-opacity-25">${value}</span>`);
        plainText = plainText.replace(regex, value);
    }
    
    const previewEl = document.getElementById('new_preview');
    if (previewEl) previewEl.innerHTML = content || '(Type message body to preview)';
    
    const channelSelect = document.querySelector('#addTemplateCard select[name="channel"]');
    const channel = channelSelect ? channelSelect.value.toLowerCase() : 'sms';
    
    const lengthSpan = document.getElementById('new_preview_len');
    const warningSpan = document.getElementById('new_preview_sms_warning');
    const statsContainer = lengthSpan ? lengthSpan.closest('.d-flex') : null;
    
    if (channel === 'email') {
        if (statsContainer) statsContainer.classList.add('d-none');
    } else {
        if (statsContainer) statsContainer.classList.remove('d-none');
        
        const stats = countSmsCharacters(plainText);
        if (lengthSpan) lengthSpan.textContent = stats.count;
        if (warningSpan) {
            if (stats.count > 160) {
                warningSpan.classList.remove('d-none');
            } else {
                warningSpan.classList.add('d-none');
            }
        }
    }
}

function translateNewTemplate(languageSelectId) {
    if (!newQuill) return;
    
    const currentHtml = newQuill.root.innerHTML;
    if (!currentHtml || currentHtml === '<p><br></p>') return;
    
    const select = document.getElementById(languageSelectId);
    let targetLang = select ? select.value : 'pt';
    
    const langMap = { 'en': 'en', 'pt': 'pt-PT', 'pt-br': 'pt', 'es': 'es' };
    targetLang = langMap[targetLang] || targetLang;
    
    if (targetLang === 'en') {
        alert("Language is already set to English, or you cannot translate English to English.");
        return;
    }
    
    const btn = document.getElementById('btn-translate-new');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Translating...';
    btn.disabled = true;
    
    fetch(`/dashboard/api/templates/0/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body: currentHtml })
    })
    .then(res => res.json())
    .then(data => {
        if (data.translated_body) {
            newQuill.clipboard.dangerouslyPasteHTML(data.translated_body);
        } else if (data.error) {
            alert('Error: ' + data.error);
        }
    })
    .finally(() => {
        btn.innerHTML = originalText;
        btn.disabled = false;
    });
}
