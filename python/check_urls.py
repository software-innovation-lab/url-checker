#!/usr/bin/env python3
"""
URL Checker Script
Reads URLs from CSV file, validates them using headless browser, and generates a report table.
"""

import csv
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Configuration
INPUT_CSV = "data/framework-sources.csv"
OUTPUT_DIR = "output"
REPORT_CSV = f"{OUTPUT_DIR}/url_check_report.csv"
TIMEOUT = 30  # seconds


def check_url_fallback_methods(url: str) -> tuple[str, str, str]:
    """
    Fallback method using command-line tools (curl, wget) when Playwright fails.
    Tries multiple methods to detect if site is accessible via different approaches.
    
    Args:
        url: The URL to check
        
    Returns:
        Tuple of (status_level, status_code, status_message)
    """
    # Try different command-line tools with various configurations
    fallback_commands = [
        # curl with HTTP/1.1 and realistic headers
        [
            'curl', '-I', '--http1.1', '-L', '--max-time', str(TIMEOUT),
            '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            url
        ],
        # curl with HTTP/2 and realistic headers
        [
            'curl', '-I', '--http2', '-L', '--max-time', str(TIMEOUT),
            '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            url
        ],
        # wget spider check
        [
            'wget', '--spider', '--timeout=' + str(TIMEOUT), '--tries=1',
            '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            url
        ]
    ]
    
    for cmd in fallback_commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TIMEOUT + 5
            )
            
            # Check curl output
            if cmd[0] == 'curl':
                output = result.stdout + result.stderr
                
                # Look for HTTP status code
                if 'HTTP/' in output:
                    lines = output.split('\n')
                    for line in lines:
                        if 'HTTP/' in line:
                            parts = line.split()
                            if len(parts) >= 2:
                                status_code = parts[1]
                                if status_code.isdigit():
                                    code = int(status_code)
                                    if 200 <= code < 300:
                                        return "warning", str(code), "OK (curl fallback - blocks browsers)"
                                    elif code == 403:
                                        return "warning", "403", "Accessible but blocks automation"
                                    elif code == 404:
                                        return "fail", "404", "Not Found (curl)"
                                    else:
                                        return "warning", str(code), f"HTTP {code} (curl)"
            
            # Check wget output
            elif cmd[0] == 'wget':
                if result.returncode == 0:
                    return "warning", "200", "OK (wget fallback - blocks browsers)"
                elif 'HTTP' in result.stderr:
                    if '404' in result.stderr:
                        return "fail", "404", "Not Found (wget)"
                    elif '403' in result.stderr:
                        return "warning", "403", "Accessible but blocks automation"
                        
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue
    
    # All methods failed
    return "fail", "ERROR", "All fallback methods failed"


def check_url(url: str, browser_context) -> tuple[str, str, str]:
    """
    Check if a URL is valid and accessible using headless browser.
    Handles both web pages and file downloads (PDFs, etc.).
    
    Args:
        url: The URL to check
        browser_context: Playwright browser context
        
    Returns:
        Tuple of (status_level, status_code, status_message)
        status_level: 'success', 'warning', or 'fail'
        status_code: HTTP status code or error type
        status_message: Detailed message
    """
    page = None
    try:
        # Create a new page for this check
        page = browser_context.new_page()
        
        # Track if a download starts (for PDFs and other files)
        download_started = False
        def handle_download(download):
            nonlocal download_started
            download_started = True
        
        page.on("download", handle_download)
        
        # Navigate to URL with timeout
        # Use 'commit' wait state which is faster and works for downloads
        response = page.goto(url, timeout=TIMEOUT * 1000, wait_until='commit')
        
        # Give a moment for download to trigger if it's a file
        page.wait_for_timeout(1000)
        
        # If download started, it's a valid file URL
        if download_started:
            if page:
                page.close()
            return "success", "200", "File Download"
        
        # Otherwise check the response status
        if response:
            status = response.status
            if page:
                page.close()
            if 200 <= status < 300:
                return "success", str(status), "OK"
            elif status == 403:
                return "warning", "403", "Access Restricted (URL exists but blocks automation)"
            else:
                return "fail", str(status), f"HTTP Error"
        else:
            if page:
                page.close()
            return "fail", "N/A", "No response received"
            
    except PlaywrightTimeout:
        if page:
            page.close()
        # Try fallback methods when Playwright times out
        print(f"  → Trying fallback methods...")
        return check_url_fallback_methods(url)
    except Exception as e:
        if page:
            page.close()
        error_msg = str(e)
        # Download starting is actually success for file URLs
        if "Download is starting" in error_msg:
            return "success", "200", "File Download"
        elif "net::ERR_CERT" in error_msg or "SSL" in error_msg or "certificate" in error_msg.lower():
            # Try fallback for SSL errors
            print(f"  → SSL error, trying fallback methods...")
            return check_url_fallback_methods(url)
        elif "net::ERR_NAME_NOT_RESOLVED" in error_msg:
            return "fail", "DNS_ERROR", "DNS Resolution Failed"
        elif "net::ERR_CONNECTION_REFUSED" in error_msg:
            return "fail", "REFUSED", "Connection Refused"
        elif "ERR_HTTP2_PROTOCOL_ERROR" in error_msg or "ERR_CONNECTION_RESET" in error_msg:
            # Try fallback for HTTP2 and connection errors
            print(f"  → Connection error, trying fallback methods...")
            return check_url_fallback_methods(url)
        else:
            # Try fallback for other errors
            print(f"  → Error occurred, trying fallback methods...")
            return check_url_fallback_methods(url)


def read_input_csv(filepath: str) -> List[Dict[str, str]]:
    """
    Read the input CSV file with framework data.
    
    Args:
        filepath: Path to the CSV file
        
    Returns:
        List of dictionaries containing framework data
    """
    frameworks = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            frameworks.append({
                'name': row['Framework Name'].strip(),
                'url': row['Verified URL'].strip(),
                'last_verified': row.get('Verified Date', '').strip(),
                'wave': row.get('Wave', '').strip()
            })
    
    return frameworks


def load_previous_report(filepath: str) -> Dict[str, Dict[str, str]]:
    """
    Load the previous report to get last valid dates.
    
    Args:
        filepath: Path to the previous report CSV
        
    Returns:
        Dictionary mapping framework names to their data
    """
    previous_data = {}
    
    if not Path(filepath).exists():
        return previous_data
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                previous_data[row['Framework Name']] = {
                    'last_valid_date': row.get('Last Valid Date', ''),
                    'validity': row.get('Validity', '')
                }
    except Exception as e:
        print(f"Warning: Could not load previous report: {e}")
    
    return previous_data


def generate_readme(results: List[Dict[str, str]], check_date: str):
    """
    Generate README.md with results table sorted by status.
    
    Args:
        results: List of check results
        check_date: Timestamp of the check
    """
    # Separate by status
    fail_results = [r for r in results if r['Status'] == 'Fail']
    warning_results = [r for r in results if r['Status'] == 'Warning']
    success_results = [r for r in results if r['Status'] == 'Success']
    
    # Sort each group by framework name
    fail_results.sort(key=lambda x: x['Framework Name'])
    warning_results.sort(key=lambda x: x['Framework Name'])
    success_results.sort(key=lambda x: x['Framework Name'])
    
    # Combine in order: Fail, Warning, Success
    sorted_results = fail_results + warning_results + success_results
    
    readme_content = f"""# URL Checker

Automated URL validation for framework sources using headless browser.

**Last Updated:** {check_date}

## Summary

- **Total URLs:** {len(results)}
- **✅ Success:** {len(success_results)}
- **⚠️  Warning:** {len(warning_results)} (URL exists but blocks automation)
- **❌ Fail:** {len(fail_results)}

---

## URL Status Report

| Status | Framework Name | Code | URL | Last Valid |
|--------|----------------|------|-----|------------|
"""
    
    # Add all results in a single table
    for result in sorted_results:
        status = result['Status']
        name = result['Framework Name']
        code = result['Status Code']
        message = result['Status Message']
        url = result['Framework URL']
        last_valid = result['Last Valid Date']
        
        # Add emoji based on status with hover tooltip
        if status == 'Success':
            status_emoji = f'<span title="{message}">✅</span>'
        elif status == 'Warning':
            status_emoji = f'<span title="{message}">⚠️</span>'
        else:
            status_emoji = f'<span title="{message}">❌</span>'
        
        # Create clickable link with hover tooltip for URL
        url_link = f'<a href="{url}" target="_blank" title="{url}">link</a>'
        
        # Create code with hover tooltip for message
        code_with_tooltip = f'<span title="{message}">{code}</span>'
        
        readme_content += f"| {status_emoji} | {name} | {code_with_tooltip} | {url_link} | {last_valid} |\n"
    
    readme_content += """
---

## Status Definitions

- **✅ Success**: URL is accessible and returns valid content
- **⚠️ Warning**: URL exists but blocks automated access (HTTP 403) - likely valid but protected
- **❌ Fail**: URL is not accessible (DNS error, timeout, SSL error, etc.)

## About

This report is automatically generated by the URL Checker CI/CD pipeline using Playwright headless browser.
- **Schedule:** Daily at 2 AM UTC
- **Source:** `data/framework-sources.csv`
- **Full Report:** `output/url_check_report.csv`
- **Technology:** Playwright Chromium (handles JavaScript, file downloads, and modern web features)
"""
    
    # Write README
    with open('README.md', 'w', encoding='utf-8') as f:
        f.write(readme_content)
    
    print(f"\nREADME.md updated with results")


def generate_report(frameworks: List[Dict[str, str]], output_path: str):
    """
    Check all URLs and generate a report CSV using headless browser.
    
    Args:
        frameworks: List of framework data
        output_path: Path to save the report
    """
    # Create output directory if it doesn't exist
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Load previous report data
    previous_data = load_previous_report(output_path)
    
    # Current timestamp
    current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    results = []
    
    print(f"\nChecking {len(frameworks)} URLs with headless browser...\n")
    print(f"{'Framework':<50} {'Status':<20} {'Message':<30}")
    print("-" * 100)
    
    # Start playwright and browser with stealth settings
    with sync_playwright() as playwright:
        # Launch with anti-detection arguments
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',  # Hide automation
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials'
            ]
        )
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=False,  # Keep SSL validation for security
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0'
            }
        )
        
        # Add stealth JavaScript to hide webdriver property
        context.add_init_script("""
            // Overwrite the `navigator.webdriver` property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Mock plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // Mock languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Add chrome object
            window.chrome = {
                runtime: {}
            };
            
            // Mock permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        for idx, framework in enumerate(frameworks, 1):
            name = framework['name']
            url = framework['url']
            
            # Check URL validity
            status_level, status_code, status_message = check_url(url, context)
            
            # Determine last valid date
            if status_level == 'success':
                last_valid_date = current_date
            else:
                # Use previous last valid date if available
                prev_data = previous_data.get(name, {})
                last_valid_date = prev_data.get('last_valid_date', 'Never')
                # Strip time portion if present (for backward compatibility)
                if last_valid_date != 'Never' and ' ' in last_valid_date:
                    last_valid_date = last_valid_date.split(' ')[0]
            
            # Print progress with emoji
            if status_level == 'success':
                status_emoji = "✅ SUCCESS"
            elif status_level == 'warning':
                status_emoji = "⚠️  WARNING"
            else:
                status_emoji = "❌ FAIL"
            
            print(f"{name:<50} {status_emoji:<20} {status_code:<12} {status_message:<30}")
            
            results.append({
                'Framework Name': name,
                'Framework URL': url,
                'Status': status_level.capitalize(),
                'Status Code': status_code,
                'Status Message': status_message,
                'Last Checked Date': current_date,
                'Last Valid Date': last_valid_date,
                'Wave': framework.get('wave', '')
            })
        
        # Close browser
        context.close()
        browser.close()
    
    # Write results to CSV
    fieldnames = [
        'Framework Name',
        'Framework URL',
        'Status',
        'Status Code',
        'Status Message',
        'Last Checked Date',
        'Last Valid Date',
        'Wave'
    ]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    # Generate README with results
    generate_readme(results, current_date)
    
    # Print summary
    success_count = sum(1 for r in results if r['Status'] == 'Success')
    warning_count = sum(1 for r in results if r['Status'] == 'Warning')
    fail_count = sum(1 for r in results if r['Status'] == 'Fail')
    
    print("\n" + "=" * 100)
    print(f"\nSummary:")
    print(f"  Total URLs checked: {len(results)}")
    print(f"  ✅ Success: {success_count}")
    print(f"  ⚠️  Warning: {warning_count}")
    print(f"  ❌ Fail: {fail_count}")
    print(f"\nReport saved to: {output_path}")
    
    # Return exit code based on results (warnings don't fail the build)
    return 0 if fail_count == 0 else 1


def main():
    """Main execution function."""
    try:
        print("=" * 100)
        print("URL Checker - Framework URL Validation")
        print("=" * 100)
        
        # Check if input file exists
        if not Path(INPUT_CSV).exists():
            print(f"Error: Input file not found: {INPUT_CSV}")
            return 1
        
        # Read input CSV
        frameworks = read_input_csv(INPUT_CSV)
        
        if not frameworks:
            print("Error: No frameworks found in input CSV")
            return 1
        
        # Generate report
        exit_code = generate_report(frameworks, REPORT_CSV)
        
        return exit_code
        
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

# Made with Bob
