#!/usr/bin/env python3
"""
Mock lab server for testing multi-tab navigation and screenshot capture.

Simulates a lab environment with:
- /           Lab guide page (sidebar + instructions + Launch button)
- /app        Admin app (opens in new tab via target=_blank)
- /app/users  User list page
- /app/settings Settings page with a form
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import sys
import threading

PORT = 8765

PAGES = {
    "/": """<!DOCTYPE html>
<html><head><title>Lab Guide - Identity Security</title>
<style>
body { font-family: -apple-system, sans-serif; margin: 0; display: flex; }
.sidebar { width: 250px; background: #1e293b; color: white; padding: 20px; min-height: 100vh; }
.sidebar h2 { font-size: 16px; margin-bottom: 16px; }
.sidebar a { color: #94a3b8; display: block; padding: 6px 0; text-decoration: none; font-size: 14px; }
.sidebar a:hover { color: white; }
.sidebar a.active { color: #3b82f6; font-weight: bold; }
.main { flex: 1; padding: 40px; max-width: 800px; }
.main h1 { font-size: 24px; margin-bottom: 8px; }
.main h2 { font-size: 18px; margin-top: 24px; color: #1e293b; }
.main p, .main li { color: #475569; line-height: 1.7; }
.main ol { padding-left: 20px; }
.launch-panel { background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin: 20px 0; }
.launch-panel h3 { margin: 0 0 12px 0; font-size: 15px; }
.launch-btn { background: #3b82f6; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }
.launch-btn:hover { background: #2563eb; }
.note { background: #fef3c7; border-left: 3px solid #f59e0b; padding: 12px 16px; margin: 16px 0; font-size: 14px; }
</style></head><body>
<div class="sidebar">
    <h2>Lab: Identity Security</h2>
    <a class="active" href="/">Initial Setup</a>
    <a href="/">Review Users</a>
    <a href="/">Configure MFA Policy</a>
    <a href="/">Verify Settings</a>
    <a href="/">Conclusion</a>
</div>
<div class="main">
    <h1>Lab: Identity Security Configuration</h1>
    <p>In this lab, you will configure security settings for your organization.</p>

    <h2>Step 1: Launch the Admin Console</h2>
    <div class="launch-panel">
        <h3>Admin Console</h3>
        <p>Click Launch to open the Admin Console in a new tab.</p>
        <a class="launch-btn" href="/app" target="_blank">Launch</a>
    </div>

    <h2>Step 2: Review the Dashboard</h2>
    <ol>
        <li>In the Admin Console, review the dashboard overview</li>
        <li>Note the number of active users and applications</li>
    </ol>

    <h2>Step 3: View Users</h2>
    <ol>
        <li>Click <strong>Users</strong> in the navigation bar</li>
        <li>Review the list of users in the system</li>
    </ol>

    <h2>Step 4: Configure Settings</h2>
    <ol>
        <li>Click <strong>Settings</strong> in the navigation bar</li>
        <li>Set the <strong>Organization Name</strong> to <code>TaskVantage</code></li>
        <li>Set the <strong>Session Timeout</strong> to <code>30</code> minutes</li>
        <li>Click <strong>Save Changes</strong></li>
    </ol>

    <div class="note">
        <strong>Note:</strong> After saving, verify the success message appears.
    </div>

    <h2>Step 5: Return and Verify</h2>
    <ol>
        <li>Return to the Dashboard</li>
        <li>Confirm the settings have been applied</li>
    </ol>
</div>
</body></html>""",

    "/app": """<!DOCTYPE html>
<html><head><title>Admin Console - Dashboard</title>
<style>
body { font-family: -apple-system, sans-serif; margin: 0; }
nav { background: #1e293b; color: white; padding: 12px 24px; display: flex; align-items: center; gap: 24px; }
nav .logo { font-weight: bold; font-size: 18px; }
nav a { color: #94a3b8; text-decoration: none; font-size: 14px; }
nav a:hover, nav a.active { color: white; }
.content { padding: 40px; }
.content h1 { font-size: 22px; margin-bottom: 24px; }
.stats { display: flex; gap: 20px; margin-bottom: 30px; }
.stat-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; flex: 1; }
.stat-card .number { font-size: 32px; font-weight: bold; color: #1e293b; }
.stat-card .label { color: #64748b; font-size: 13px; margin-top: 4px; }
.activity { margin-top: 20px; }
.activity h2 { font-size: 16px; margin-bottom: 12px; }
.activity table { width: 100%; border-collapse: collapse; font-size: 14px; }
.activity th { text-align: left; padding: 8px 12px; background: #f8fafc; border-bottom: 2px solid #e2e8f0; }
.activity td { padding: 8px 12px; border-bottom: 1px solid #f1f5f9; }
</style></head><body>
<nav>
    <span class="logo">Admin Console</span>
    <a class="active" href="/app">Dashboard</a>
    <a href="/app/users">Users</a>
    <a href="/app/settings">Settings</a>
</nav>
<div class="content">
    <h1>Dashboard</h1>
    <div class="stats">
        <div class="stat-card"><div class="number">1,247</div><div class="label">Active Users</div></div>
        <div class="stat-card"><div class="number">38</div><div class="label">Applications</div></div>
        <div class="stat-card"><div class="number">12</div><div class="label">Pending Reviews</div></div>
        <div class="stat-card"><div class="number">99.7%</div><div class="label">Uptime</div></div>
    </div>
    <div class="activity">
        <h2>Recent Activity</h2>
        <table>
            <thead><tr><th>Time</th><th>Event</th><th>User</th><th>Status</th></tr></thead>
            <tbody>
                <tr><td>2 min ago</td><td>Login</td><td>sarah.chen@taskvantage.com</td><td>Success</td></tr>
                <tr><td>5 min ago</td><td>Password Reset</td><td>marcus.johnson@taskvantage.com</td><td>Success</td></tr>
                <tr><td>12 min ago</td><td>Login</td><td>priya.patel@taskvantage.com</td><td>Failed</td></tr>
                <tr><td>18 min ago</td><td>App Assignment</td><td>david.kim@taskvantage.com</td><td>Success</td></tr>
                <tr><td>25 min ago</td><td>MFA Enrollment</td><td>emma.wilson@taskvantage.com</td><td>Success</td></tr>
            </tbody>
        </table>
    </div>
</div>
</body></html>""",

    "/app/users": """<!DOCTYPE html>
<html><head><title>Admin Console - Users</title>
<style>
body { font-family: -apple-system, sans-serif; margin: 0; }
nav { background: #1e293b; color: white; padding: 12px 24px; display: flex; align-items: center; gap: 24px; }
nav .logo { font-weight: bold; font-size: 18px; }
nav a { color: #94a3b8; text-decoration: none; font-size: 14px; }
nav a:hover, nav a.active { color: white; }
.content { padding: 40px; }
.content h1 { font-size: 22px; margin-bottom: 24px; }
.search { margin-bottom: 20px; }
.search input { padding: 8px 16px; border: 1px solid #e2e8f0; border-radius: 6px; width: 300px; font-size: 14px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; padding: 10px 12px; background: #f8fafc; border-bottom: 2px solid #e2e8f0; font-weight: 600; }
td { padding: 10px 12px; border-bottom: 1px solid #f1f5f9; }
.status-active { color: #22c55e; font-weight: 500; }
.status-inactive { color: #ef4444; font-weight: 500; }
</style></head><body>
<nav>
    <span class="logo">Admin Console</span>
    <a href="/app">Dashboard</a>
    <a class="active" href="/app/users">Users</a>
    <a href="/app/settings">Settings</a>
</nav>
<div class="content">
    <h1>Users</h1>
    <div class="search"><input type="text" placeholder="Search users..." name="search"></div>
    <table>
        <thead><tr><th>Name</th><th>Email</th><th>Department</th><th>Role</th><th>Status</th></tr></thead>
        <tbody>
            <tr><td>Sarah Chen</td><td>sarah.chen@taskvantage.com</td><td>Engineering</td><td>Admin</td><td class="status-active">Active</td></tr>
            <tr><td>Marcus Johnson</td><td>marcus.johnson@taskvantage.com</td><td>Engineering</td><td>User</td><td class="status-active">Active</td></tr>
            <tr><td>Priya Patel</td><td>priya.patel@taskvantage.com</td><td>Sales</td><td>User</td><td class="status-active">Active</td></tr>
            <tr><td>David Kim</td><td>david.kim@taskvantage.com</td><td>Marketing</td><td>User</td><td class="status-inactive">Inactive</td></tr>
            <tr><td>Emma Wilson</td><td>emma.wilson@taskvantage.com</td><td>Finance</td><td>Admin</td><td class="status-active">Active</td></tr>
            <tr><td>James Rodriguez</td><td>james.rodriguez@taskvantage.com</td><td>Sales</td><td>User</td><td class="status-active">Active</td></tr>
        </tbody>
    </table>
</div>
</body></html>""",

    "/app/settings": """<!DOCTYPE html>
<html><head><title>Admin Console - Settings</title>
<style>
body { font-family: -apple-system, sans-serif; margin: 0; }
nav { background: #1e293b; color: white; padding: 12px 24px; display: flex; align-items: center; gap: 24px; }
nav .logo { font-weight: bold; font-size: 18px; }
nav a { color: #94a3b8; text-decoration: none; font-size: 14px; }
nav a:hover, nav a.active { color: white; }
.content { padding: 40px; max-width: 600px; }
.content h1 { font-size: 22px; margin-bottom: 24px; }
.form-group { margin-bottom: 20px; }
.form-group label { display: block; font-size: 14px; font-weight: 500; margin-bottom: 6px; color: #334155; }
.form-group input, .form-group select { width: 100%; padding: 8px 12px; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 14px; }
.save-btn { background: #3b82f6; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; }
.save-btn:hover { background: #2563eb; }
.success-msg { display: none; background: #dcfce7; border: 1px solid #22c55e; color: #166534; padding: 12px 16px; border-radius: 6px; margin-top: 16px; font-size: 14px; }
</style></head><body>
<nav>
    <span class="logo">Admin Console</span>
    <a href="/app">Dashboard</a>
    <a href="/app/users">Users</a>
    <a class="active" href="/app/settings">Settings</a>
</nav>
<div class="content">
    <h1>Settings</h1>
    <div class="form-group">
        <label for="org-name">Organization Name</label>
        <input type="text" id="org-name" name="org_name" placeholder="Enter organization name">
    </div>
    <div class="form-group">
        <label for="session-timeout">Session Timeout (minutes)</label>
        <input type="number" id="session-timeout" name="session_timeout" placeholder="30">
    </div>
    <div class="form-group">
        <label for="mfa-policy">MFA Policy</label>
        <select id="mfa-policy" name="mfa_policy">
            <option value="optional">Optional</option>
            <option value="required">Required for all users</option>
            <option value="admin-only">Required for admins only</option>
        </select>
    </div>
    <button class="save-btn" onclick="document.getElementById('success').style.display='block'">Save Changes</button>
    <div id="success" class="success-msg">Settings saved successfully!</div>
</div>
</body></html>""",
}


class MockLabHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in PAGES:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGES[path].encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>404 Not Found</h1>")

    def log_message(self, format, *args):
        pass  # Suppress request logging


def start_server():
    server = HTTPServer(("127.0.0.1", PORT), MockLabHandler)
    print(f"Mock lab server running on http://localhost:{PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    start_server()
