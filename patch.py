import os

with open('templates/dashboard/templates.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the edit button and remove the inline form
start_idx = content.find('<!-- Actions -->')
end_idx = content.find('{% endfor %}', start_idx)

new_actions = '''<!-- Actions -->
                <td>
                  <button class="btn btn-sm btn-outline-primary py-0 px-2 small" type="button" onclick="openTemplateEditor({{ t.id }})">
                    <i class="bi bi-pencil"></i> Edit
                  </button>
                </td>
              </tr>
              '''

if start_idx != -1 and end_idx != -1:
    content = content[:start_idx] + new_actions + content[end_idx:]

# Remove script blocks and add the editor view
script_start = content.find('<script>')
if script_start != -1:
    content = content[:script_start] + '''
</div> <!-- End templates-list-view -->

<!-- Template Editor View -->
<div id="template-editor-view" class="d-none">
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h4 class="mb-0">Edit Template</h4>
    <button class="btn btn-sm btn-outline-secondary" onclick="closeTemplateEditor()">Back to List</button>
  </div>
  <div class="template-editor-wrapper">
    <div class="row">
      <!-- Left: Editor -->
      <div class="col-lg-7">
        <div class="editor-container border rounded bg-white p-3 shadow-sm h-100">
          <!-- Info Bar -->
          <div class="editor-info border-bottom pb-2 mb-3">
            <span class="badge bg-secondary" id="editor-badge-channel"></span>
            <span class="badge bg-info text-dark" id="editor-badge-rule"></span>
            <span class="badge bg-light text-dark border" id="editor-badge-lang"></span>
          </div>
          
          <!-- Subject (Email only) -->
          <div class="mb-3 d-none" id="subject-container">
            <label class="form-label fw-bold small text-muted mb-1">Subject Line</label>
            <input type="text" class="form-control form-control-sm" id="template-subject" placeholder="Enter email subject...">
          </div>
          
          <!-- Placeholder Buttons -->
          <div class="placeholder-buttons mb-3">
            <div class="small text-muted mb-1 font-weight-medium">Insert Placeholder:</div>
            <button class="btn btn-sm btn-outline-success rounded-pill py-1 px-2" onclick="insertPlaceholder('customer_name')">
              👤 Customer Name
            </button>
            <button class="btn btn-sm btn-outline-success rounded-pill py-1 px-2" onclick="insertPlaceholder('appointment_date')">
              📅 Date
            </button>
            <button class="btn btn-sm btn-outline-success rounded-pill py-1 px-2" onclick="insertPlaceholder('appointment_time')">
              ⏰ Time
            </button>
            <button class="btn btn-sm btn-outline-success rounded-pill py-1 px-2" onclick="insertPlaceholder('appointment_type')">
              📋 Service Type
            </button>
            <button class="btn btn-sm btn-outline-success rounded-pill py-1 px-2" onclick="insertPlaceholder('location')">
              📍 Location
            </button>
          </div>
          
          <!-- Quill Editor -->
          <div id="editor" style="height: 300px;"></div>
          
          <!-- Editor Footer -->
          <div class="editor-footer d-flex flex-column flex-md-row justify-content-between align-items-md-center mt-3 gap-2">
            <div id="char-counter" class="text-muted small">0 characters (0 segments)</div>
            <div class="d-flex flex-wrap gap-2 justify-content-end">
              <button id="btn-translate" class="btn btn-sm btn-outline-secondary" onclick="translateTemplate()">
                <i class="bi bi-translate"></i> Auto-Translate
              </button>
              <button class="btn btn-sm btn-outline-warning" onclick="revertTemplate()">
                ↺ Revert Default
              </button>
              <button id="btn-save" class="btn btn-sm btn-success text-white px-3 fw-medium" onclick="saveTemplate()">
                Save Template
              </button>
            </div>
          </div>
        </div>
      </div>
      
      <!-- Right: Preview -->
      <div class="col-lg-5">
        <div class="preview-container d-flex flex-column h-100">
          <h6 class="text-success font-weight-bold mb-3"><i class="bi bi-eye-fill"></i> Live Message Preview</h6>
          <div id="preview-content" class="preview-content flex-grow-1 shadow-sm font-monospace text-dark">
            <!-- Preview rendered here -->
          </div>
          <div class="preview-actions mt-3 text-end border-top pt-3">
            <button id="btn-test-send" class="btn btn-sm btn-primary px-3 fw-medium" onclick="sendTest()">
              <i class="bi bi-send-fill"></i> Send Test to Me
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

{% endblock %}
'''

with open('templates/dashboard/templates.html', 'w', encoding='utf-8') as f:
    f.write(content)
