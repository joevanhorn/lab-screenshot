#!/usr/bin/env python3
"""
Mock Okta Admin Console server for testing selectize dropdowns,
SimpleModal confirmations, collapsible sidebar navigation, and
API endpoints.

Simulates the key Okta UI patterns that the bot struggles with.
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import sys
import threading

PORT = 8766

# Common Okta-like styles
OKTA_CSS = """
body { font-family: -apple-system, sans-serif; margin: 0; display: flex; min-height: 100vh; }
.sidenav { width: 220px; background: #1a1a2e; color: #b0b0c0; padding: 0; flex-shrink: 0; }
.sidenav .logo { padding: 16px 20px; font-weight: bold; color: white; font-size: 16px; border-bottom: 1px solid #2a2a4e; }
.sidenav ul { list-style: none; padding: 0; margin: 0; }
.sidenav li { border-bottom: 1px solid #2a2a4e; }
.sidenav li > a { display: flex; justify-content: space-between; align-items: center; padding: 10px 20px; color: #b0b0c0; text-decoration: none; font-size: 14px; cursor: pointer; }
.sidenav li > a:hover { background: #2a2a4e; color: white; }
.sidenav li > a.active { color: #5ce0d8; background: #2a2a4e; }
.sidenav .chevron { font-size: 10px; transition: transform 0.2s; }
.sidenav .chevron.open { transform: rotate(90deg); }
.sidenav .submenu { display: none; background: #12122a; }
.sidenav .submenu.open { display: block; }
.sidenav .submenu a { padding: 8px 20px 8px 36px; display: block; color: #8080a0; text-decoration: none; font-size: 13px; }
.sidenav .submenu a:hover { color: white; background: #1a1a2e; }
.sidenav .submenu a.active { color: #5ce0d8; }

.main-content { flex: 1; overflow-y: auto; }
.page-header { padding: 24px 32px; border-bottom: 1px solid #e0e0e0; }
.page-header h1 { margin: 0; font-size: 20px; }
.page-body { padding: 24px 32px; }

/* Selectize-like dropdown */
.selectize-wrapper { position: relative; max-width: 400px; }
.selectize-wrapper select.selectized { display: none; }
.selectize-input { background: white; border: 1px solid #ccc; border-radius: 4px; padding: 8px 12px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
.selectize-input .item { color: #333; }
.selectize-input .arrow { color: #999; font-size: 10px; }
.selectize-dropdown { display: none; position: absolute; top: 100%; left: 0; right: 0; background: white; border: 1px solid #ccc; border-top: none; border-radius: 0 0 4px 4px; z-index: 100; }
.selectize-dropdown.open { display: block; }
.selectize-dropdown .option { padding: 8px 12px; cursor: pointer; font-size: 14px; }
.selectize-dropdown .option:hover { background: #e8f0fe; }
.selectize-dropdown .option.selected { background: #d0e0ff; }

/* Policy rules table */
.rules-table { width: 100%; border-collapse: collapse; margin-top: 16px; }
.rules-table th { text-align: left; padding: 8px 12px; border-bottom: 2px solid #e0e0e0; font-size: 13px; color: #666; }
.rules-table td { padding: 8px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
.rules-table .actions-btn { background: none; border: 1px solid #ccc; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 13px; }
.actions-dropdown { display: none; position: absolute; background: white; border: 1px solid #ccc; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); z-index: 50; }
.actions-dropdown.open { display: block; }
.actions-dropdown a { display: block; padding: 8px 16px; color: #333; text-decoration: none; font-size: 13px; }
.actions-dropdown a:hover { background: #f0f0f0; }

/* SimpleModal overlay + container */
#simplemodal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 1000; }
#simplemodal-container { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: white; border-radius: 8px; padding: 24px; width: 500px; max-height: 80vh; overflow-y: auto; z-index: 1001; }
#simplemodal-container.visible, #simplemodal-overlay.visible { display: block; }
#simplemodal-container h3 { margin: 0 0 12px 0; }
#simplemodal-container .warning { background: #fef3c7; border-left: 3px solid #f59e0b; padding: 12px; margin: 12px 0; font-size: 13px; }
#simplemodal-container .btn-row { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
#simplemodal-container .btn { padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; border: 1px solid #ccc; }
#simplemodal-container .btn-primary { background: #3b82f6; color: white; border-color: #3b82f6; }
#simplemodal-container .btn-cancel { background: white; }

/* Edit Rule dialog (MUI-like) */
.edit-dialog-backdrop { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.3); z-index: 500; }
.edit-dialog { display: none; position: fixed; top: 5%; left: 50%; transform: translateX(-50%); background: white; border-radius: 8px; width: 600px; max-height: 90vh; overflow-y: auto; z-index: 501; box-shadow: 0 4px 20px rgba(0,0,0,0.2); }
.edit-dialog.visible, .edit-dialog-backdrop.visible { display: block; }
.edit-dialog .dialog-header { padding: 16px 24px; border-bottom: 1px solid #e0e0e0; }
.edit-dialog .dialog-header h2 { margin: 0; font-size: 18px; }
.edit-dialog .dialog-body { padding: 24px; }
.edit-dialog .dialog-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 8px; }
.edit-dialog .section-label { font-size: 12px; font-weight: bold; color: #666; margin: 16px 0 8px; }
.edit-dialog .form-row { margin-bottom: 12px; }
.edit-dialog .form-row label { display: block; font-size: 13px; color: #444; margin-bottom: 4px; }
.btn { padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 14px; border: 1px solid #ccc; background: white; }
.btn-primary { background: #3b82f6; color: white; border-color: #3b82f6; }
.btn-primary:hover { background: #2563eb; }
.status-enabled { background: #dcfce7; color: #166534; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
"""

POLICIES_PAGE = f"""<!DOCTYPE html>
<html><head><title>Okta Admin - Authentication Policies</title>
<style>{OKTA_CSS}</style></head><body>
<div class="sidenav">
    <div class="logo">okta Admin Console</div>
    <ul>
        <li><a href="/admin/dashboard">Dashboard</a></li>
        <li><a href="/admin/users">Directory ›</a></li>
        <li>
            <a onclick="toggleSubmenu(this)" class="active">Security <span class="chevron open">›</span></a>
            <div class="submenu open">
                <a href="/admin/authenticators">Authenticators</a>
                <a href="/admin/authentication-policies" class="active">Authentication Policies</a>
                <a href="/admin/global-session">Global Session Policy</a>
            </div>
        </li>
        <li>
            <a onclick="toggleSubmenu(this)">Reports <span class="chevron">›</span></a>
            <div class="submenu">
                <a href="/admin/system-log">System Log</a>
            </div>
        </li>
    </ul>
</div>
<div class="main-content">
    <div class="page-header"><h1>TaskVantage - Apps</h1></div>
    <div class="page-body">
        <p style="color:#666;font-size:14px">Rules <strong>3</strong></p>
        <table class="rules-table">
            <thead><tr><th>Priority</th><th>Rule</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
                <tr>
                    <td>1</td>
                    <td><strong>Employee Access</strong><br><span style="color:#666;font-size:12px">THEN Access: Allowed with password</span></td>
                    <td><span class="status-enabled">ENABLED</span></td>
                    <td style="position:relative">
                        <button class="actions-btn" onclick="toggleActions(this)">Actions ▼</button>
                        <div class="actions-dropdown">
                            <a href="#" onclick="openEditDialog(); return false;">Edit</a>
                            <a href="#">Deactivate</a>
                            <a href="#">Delete</a>
                        </div>
                    </td>
                </tr>
                <tr>
                    <td>2</td>
                    <td><strong>Contractor Access</strong><br><span style="color:#666;font-size:12px">THEN Access: Allowed with password</span></td>
                    <td><span class="status-enabled">ENABLED</span></td>
                    <td style="position:relative">
                        <button class="actions-btn" onclick="toggleActions(this)">Actions ▼</button>
                        <div class="actions-dropdown">
                            <a href="#">Edit</a>
                            <a href="#">Deactivate</a>
                        </div>
                    </td>
                </tr>
                <tr>
                    <td>3</td>
                    <td><strong>Catch-all Rule</strong><br><span style="color:#666;font-size:12px">THEN Access: Denied</span></td>
                    <td><span class="status-enabled">ENABLED</span></td>
                    <td style="position:relative">
                        <button class="actions-btn" onclick="toggleActions(this)">Actions ▼</button>
                        <div class="actions-dropdown">
                            <a href="#">Edit</a>
                            <a href="#">Deactivate</a>
                        </div>
                    </td>
                </tr>
            </tbody>
        </table>
    </div>
</div>

<!-- Edit Rule Dialog -->
<div class="edit-dialog-backdrop" role="dialog" aria-modal="true" id="edit-backdrop"></div>
<div class="edit-dialog" role="dialog" id="edit-dialog">
    <div class="dialog-header"><h2>Edit Rule</h2></div>
    <div class="dialog-body">
        <div class="form-row">
            <label>Rule name</label>
            <input type="text" value="Employee Access" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:4px">
        </div>
        <div class="section-label">IF</div>
        <div class="form-row">
            <label>AND User's user type is</label>
            <div class="selectize-wrapper">
                <select class="selectized" name="userType">
                    <option value="any" selected>Any user type</option>
                    <option value="one">One specific type</option>
                </select>
                <div class="selectize-input" onclick="toggleSelectize(this)">
                    <span class="item">Any user type</span>
                    <span class="arrow">▼</span>
                </div>
                <div class="selectize-dropdown">
                    <div class="option selected" onclick="selectOption(this, 'any')">Any user type</div>
                    <div class="option" onclick="selectOption(this, 'one')">One specific type</div>
                </div>
            </div>
        </div>
        <div class="form-row">
            <label>AND User's group membership includes</label>
            <input type="text" value="Employees" style="width:100%;padding:8px;border:1px solid #ccc;border-radius:4px" readonly>
        </div>

        <!-- Lots of IF conditions to make the form tall (need scrolling) -->
        <div class="form-row"><label>AND User Is</label><span style="color:#666">Any user</span></div>
        <div class="form-row"><label>AND Device state is</label><span style="color:#666">Any</span></div>
        <div class="form-row"><label>AND Device assurance policy is</label><span style="color:#666">No policy</span></div>
        <div class="form-row"><label>AND Device platform is</label><span style="color:#666">Any platform</span></div>
        <div class="form-row"><label>AND User's IP is</label><span style="color:#666">Any IP</span></div>
        <div class="form-row"><label>AND Risk is</label><span style="color:#666">Any</span></div>

        <div class="section-label" data-se="then-section">THEN</div>
        <div class="form-row" data-se="o-form-fieldset">
            <label>THEN Access is</label>
            <div style="margin-top:4px">
                <label><input type="radio" name="access" value="denied"> Denied</label><br>
                <label><input type="radio" name="access" value="allowed" checked> Allowed after successful authentication</label>
            </div>
        </div>
        <div class="form-row" data-se="o-form-fieldset">
            <label>AND User must authenticate with</label>
            <div class="selectize-wrapper">
                <select class="selectized" name="verificationMethod.type">
                    <option value="PASSWORD" selected>Password</option>
                    <option value="PASSWORD_AND_ANOTHER">Password + Another factor</option>
                    <option value="POSSESSION">Possession factor</option>
                    <option value="ANY_TWO">Any 2 factor types</option>
                </select>
                <div class="selectize-input" onclick="toggleSelectize(this)">
                    <span class="item">Password</span>
                    <span class="arrow">▼</span>
                </div>
                <div class="selectize-dropdown">
                    <div class="option selected" onclick="selectOption(this, 'PASSWORD')">Password</div>
                    <div class="option" onclick="selectOption(this, 'PASSWORD_AND_ANOTHER')">Password + Another factor</div>
                    <div class="option" onclick="selectOption(this, 'POSSESSION')">Possession factor</div>
                    <div class="option" onclick="selectOption(this, 'ANY_TWO')">Any 2 factor types</div>
                </div>
            </div>
        </div>
        <div class="form-row"><label>AND Possession factor constraints are</label><span style="color:#666">Phishing Resistant Disabled, Hardware Protected Disabled</span></div>
        <div class="form-row"><label>AND Authentication methods</label><span style="color:#666">Allow any method that can be used to meet the requirement</span></div>
        <div class="form-row"><label>AND Option to stay signed in</label><span style="color:#666">Show after users sign in: Disabled</span></div>
    </div>
    <div class="dialog-footer">
        <button class="btn btn-cancel" onclick="closeEditDialog()">Cancel</button>
        <button class="btn btn-primary" data-se="save" onclick="showSaveConfirmation()">Save</button>
    </div>
</div>

<!-- SimpleModal confirmation (shown after clicking Save) -->
<div id="simplemodal-overlay"></div>
<div id="simplemodal-container" role="dialog" aria-modal="true">
    <h3>Are you sure you want to save this rule?</h3>
    <div class="warning">
        Note: if you still want to save this rule, click "Save anyway". Users with weak authenticators may still be able to access apps with this policy.
    </div>
    <div class="btn-row">
        <button class="btn btn-primary" onclick="confirmSave()">Save anyway</button>
        <button class="btn btn-cancel" onclick="cancelSave()">Cancel</button>
    </div>
</div>

<script>
function toggleSubmenu(el) {{
    const submenu = el.nextElementSibling;
    const chevron = el.querySelector('.chevron');
    submenu.classList.toggle('open');
    chevron.classList.toggle('open');
}}

function toggleActions(btn) {{
    // Close all other dropdowns
    document.querySelectorAll('.actions-dropdown.open').forEach(d => d.classList.remove('open'));
    const dropdown = btn.nextElementSibling;
    dropdown.classList.toggle('open');
}}

function toggleSelectize(el) {{
    const dropdown = el.nextElementSibling;
    // Close all other selectize dropdowns
    document.querySelectorAll('.selectize-dropdown.open').forEach(d => {{ if (d !== dropdown) d.classList.remove('open'); }});
    dropdown.classList.toggle('open');
}}

function selectOption(optionEl, value) {{
    const wrapper = optionEl.closest('.selectize-wrapper');
    const input = wrapper.querySelector('.selectize-input .item');
    const select = wrapper.querySelector('select');
    const dropdown = wrapper.querySelector('.selectize-dropdown');

    // Update display
    input.textContent = optionEl.textContent;

    // Update hidden select
    select.value = value;
    select.dispatchEvent(new Event('change', {{bubbles: true}}));

    // Update selected state
    dropdown.querySelectorAll('.option').forEach(o => o.classList.remove('selected'));
    optionEl.classList.add('selected');

    // Close dropdown
    dropdown.classList.remove('open');
}}

function openEditDialog() {{
    // Close any actions dropdown
    document.querySelectorAll('.actions-dropdown.open').forEach(d => d.classList.remove('open'));
    document.getElementById('edit-dialog').classList.add('visible');
    document.getElementById('edit-backdrop').classList.add('visible');
}}

function closeEditDialog() {{
    document.getElementById('edit-dialog').classList.remove('visible');
    document.getElementById('edit-backdrop').classList.remove('visible');
}}

function showSaveConfirmation() {{
    document.getElementById('simplemodal-overlay').classList.add('visible');
    document.getElementById('simplemodal-container').classList.add('visible');
}}

function confirmSave() {{
    // Close both dialogs
    document.getElementById('simplemodal-overlay').classList.remove('visible');
    document.getElementById('simplemodal-container').classList.remove('visible');
    closeEditDialog();

    // Update the rule display
    const ruleDesc = document.querySelector('.rules-table tbody tr td:nth-child(2) span');
    const authSelect = document.querySelector('select[name="verificationMethod.type"]');
    const labels = {{'PASSWORD': 'password', 'PASSWORD_AND_ANOTHER': 'Password + Another factor', 'POSSESSION': 'Possession factor', 'ANY_TWO': 'Any 2 factor types'}};
    ruleDesc.textContent = 'THEN Access: Allowed with ' + (labels[authSelect.value] || authSelect.value);
}}

function cancelSave() {{
    document.getElementById('simplemodal-overlay').classList.remove('visible');
    document.getElementById('simplemodal-container').classList.remove('visible');
}}

// Close dropdowns when clicking outside
document.addEventListener('click', function(e) {{
    if (!e.target.closest('.actions-btn') && !e.target.closest('.actions-dropdown')) {{
        document.querySelectorAll('.actions-dropdown.open').forEach(d => d.classList.remove('open'));
    }}
}});
</script>
</body></html>"""

PAGES = {
    "/admin/authentication-policies": POLICIES_PAGE,
    "/admin/authentication-policies/app-sign-in": POLICIES_PAGE,
    "/admin/dashboard": f"""<!DOCTYPE html>
<html><head><title>Okta Admin - Dashboard</title><style>{OKTA_CSS}</style></head><body>
<div class="sidenav">
    <div class="logo">okta Admin Console</div>
    <ul>
        <li><a href="/admin/dashboard" class="active">Dashboard</a></li>
        <li><a href="/admin/users">Directory ›</a></li>
        <li>
            <a onclick="toggleSubmenu(this)">Security <span class="chevron">›</span></a>
            <div class="submenu">
                <a href="/admin/authenticators">Authenticators</a>
                <a href="/admin/authentication-policies">Authentication Policies</a>
            </div>
        </li>
        <li>
            <a onclick="toggleSubmenu(this)">Reports <span class="chevron">›</span></a>
            <div class="submenu">
                <a href="/admin/system-log">System Log</a>
            </div>
        </li>
    </ul>
</div>
<div class="main-content">
    <div class="page-header"><h1>Dashboard</h1></div>
    <div class="page-body"><p>Admin Console Dashboard</p></div>
</div>
<script>
function toggleSubmenu(el) {{
    const submenu = el.nextElementSibling;
    const chevron = el.querySelector('.chevron');
    submenu.classList.toggle('open');
    chevron.classList.toggle('open');
}}
</script>
</body></html>""",

    "/api/v1/users": '[]',
}


class MockOktaHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in PAGES:
            content = PAGES[path]
            ctype = "application/json" if path.startswith("/api/") else "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(content.encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>404 Not Found</h1>")

    def do_POST(self):
        # Mock factor enrollment
        if "/factors" in self.path:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode() if content_length else ""
            response = json.dumps({
                "id": "fac_mock_123",
                "factorType": "token:software:totp",
                "provider": "OKTA",
                "status": "PENDING_ACTIVATION",
                "_embedded": {
                    "activation": {
                        "sharedSecret": "JBSWY3DPEBLW64TMMQQQ",
                        "encoding": "base32"
                    }
                }
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_server():
    server = HTTPServer(("127.0.0.1", PORT), MockOktaHandler)
    print(f"Mock Okta server running on http://localhost:{PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    start_server()
