#!/usr/bin/env python3
"""
URL Checker Script
Reads URLs from CSV file, validates them, and generates a report table.
"""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict
import requests
from urllib.parse import urlparse

# Configuration
INPUT_CSV = "data/framework-sources.csv"
OUTPUT_DIR = "output"
REPORT_CSV = f"{OUTPUT_DIR}/url_check_report.csv"
TIMEOUT = 30  # seconds


def check_url(url: str) -> tuple[bool, str]:
    """
    Check if a URL is valid and accessible.
    
    Args:
        url: The URL to check
        
    Returns:
        Tuple of (is_valid, status_message)
    """
    try:
        # Parse URL to ensure it's well-formed
        parsed = urlparse(url)
        if not all([parsed.scheme, parsed.netloc]):
            return False, "Invalid URL format"
        
        # Make HEAD request first (faster)
        response = requests.head(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers={'User-Agent': 'URL-Checker/1.0'}
        )
        
        # If HEAD fails, try GET
        if response.status_code >= 400:
            response = requests.get(
                url,
                timeout=TIMEOUT,
                allow_redirects=True,
                headers={'User-Agent': 'URL-Checker/1.0'}
            )
        
        if response.status_code == 200:
            return True, f"OK (Status: {response.status_code})"
        elif 200 <= response.status_code < 300:
            return True, f"OK (Status: {response.status_code})"
        else:
            return False, f"HTTP {response.status_code}"
            
    except requests.exceptions.Timeout:
        return False, "Timeout"
    except requests.exceptions.ConnectionError:
        return False, "Connection Error"
    except requests.exceptions.TooManyRedirects:
        return False, "Too Many Redirects"
    except requests.exceptions.RequestException as e:
        return False, f"Request Error: {str(e)[:50]}"
    except Exception as e:
        return False, f"Error: {str(e)[:50]}"


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
    Generate README.md with results table.
    
    Args:
        results: List of check results
        check_date: Timestamp of the check
    """
    # Separate valid and invalid results
    invalid_results = [r for r in results if r['Validity'] == 'Invalid']
    valid_results = [r for r in results if r['Validity'] == 'Valid']
    
    # Sort by framework name
    invalid_results.sort(key=lambda x: x['Framework Name'])
    valid_results.sort(key=lambda x: x['Framework Name'])
    
    readme_content = f"""# URL Checker

Automated URL validation for framework sources.

**Last Updated:** {check_date}

## Summary

- **Total URLs:** {len(results)}
- **Valid URLs:** ✅ {len(valid_results)}
- **Invalid URLs:** ❌ {len(invalid_results)}

---

"""
    
    # Add invalid URLs section if any exist
    if invalid_results:
        readme_content += """## ❌ Invalid URLs

The following URLs are currently invalid and need attention:

| Framework Name | URL | Status | Last Valid Date |
|----------------|-----|--------|-----------------|
"""
        for result in invalid_results:
            name = result['Framework Name']
            url = result['Framework URL']
            status = result['Status Message']
            last_valid = result['Last Valid Date']
            readme_content += f"| {name} | {url} | {status} | {last_valid} |\n"
        
        readme_content += "\n---\n\n"
    
    # Add valid URLs section
    readme_content += """## ✅ Valid URLs

The following URLs are currently valid:

| Framework Name | URL | Last Checked |
|----------------|-----|--------------|
"""
    for result in valid_results:
        name = result['Framework Name']
        url = result['Framework URL']
        last_checked = result['Last Checked Date']
        readme_content += f"| {name} | {url} | {last_checked} |\n"
    
    readme_content += """
---

## About

This report is automatically generated by the URL Checker CI/CD pipeline.
- **Schedule:** Daily at 2 AM UTC
- **Source:** `data/framework-sources.csv`
- **Full Report:** `output/url_check_report.csv`
"""
    
    # Write README
    with open('README.md', 'w', encoding='utf-8') as f:
        f.write(readme_content)
    
    print(f"\nREADME.md updated with results")


def generate_report(frameworks: List[Dict[str, str]], output_path: str):
    """
    Check all URLs and generate a report CSV.
    
    Args:
        frameworks: List of framework data
        output_path: Path to save the report
    """
    # Create output directory if it doesn't exist
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Load previous report data
    previous_data = load_previous_report(output_path)
    
    # Current timestamp
    current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    results = []
    
    print(f"\nChecking {len(frameworks)} URLs...\n")
    print(f"{'Framework':<50} {'Status':<20} {'Message':<30}")
    print("-" * 100)
    
    for idx, framework in enumerate(frameworks, 1):
        name = framework['name']
        url = framework['url']
        
        # Check URL validity
        is_valid, message = check_url(url)
        
        # Determine last valid date
        if is_valid:
            last_valid_date = current_date
        else:
            # Use previous last valid date if available
            prev_data = previous_data.get(name, {})
            last_valid_date = prev_data.get('last_valid_date', 'Never')
        
        # Print progress
        status = "✓ VALID" if is_valid else "✗ INVALID"
        print(f"{name:<50} {status:<20} {message:<30}")
        
        results.append({
            'Framework Name': name,
            'Framework URL': url,
            'Validity': 'Valid' if is_valid else 'Invalid',
            'Status Message': message,
            'Last Checked Date': current_date,
            'Last Valid Date': last_valid_date,
            'Wave': framework.get('wave', '')
        })
    
    # Write results to CSV
    fieldnames = [
        'Framework Name',
        'Framework URL',
        'Validity',
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
    valid_count = sum(1 for r in results if r['Validity'] == 'Valid')
    invalid_count = len(results) - valid_count
    
    print("\n" + "=" * 100)
    print(f"\nSummary:")
    print(f"  Total URLs checked: {len(results)}")
    print(f"  Valid URLs: {valid_count}")
    print(f"  Invalid URLs: {invalid_count}")
    print(f"\nReport saved to: {output_path}")
    
    # Return exit code based on results
    return 0 if invalid_count == 0 else 1


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
