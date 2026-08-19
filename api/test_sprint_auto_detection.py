"""
Comprehensive test for Sprint Field Auto-Detection

Tests:
1. Sprint field detection from Jira API
2. Different field ID scenarios
3. Sync with detected field
4. Fallback mechanisms
"""
__test__ = False  # Manual integration script; not part of automated pytest suite.

import asyncio
import os
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

# Load environment variables
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            os.environ[key] = value.strip('"').strip("'")

sys.path.insert(0, os.getcwd())

from app.core.security import encryption_service
from app.integrations.jira.oauth import jira_oauth_service


async def test_sprint_field_detection():
    """Test 1: Sprint field detection from Jira API"""
    print("="*70)
    print("TEST 1: Sprint Field Auto-Detection")
    print("="*70)

    client = AsyncIOMotorClient(os.environ['MONGODB_URI'])
    db = client.get_default_database()

    # Get Test 3 project (TT)
    project = await db.projects.find_one({"_id": ObjectId("69997acd63987877734fdb65")})

    if not project:
        print("❌ Project not found")
        client.close()
        return False

    jira = project['integrations']['jira']
    access_token = encryption_service.decrypt(jira['access_token'])
    cloud_id = jira['cloud_id']

    print(f"\n📊 Testing with:")
    print(f"   Project: {project['project_name']}")
    print(f"   Jira Site: {jira['site_url']}")
    print(f"   Cloud ID: {cloud_id}")

    # Test detection
    print(f"\n🔍 Running sprint field detection...")
    detected_field_id = await jira_oauth_service.detect_sprint_field_id(access_token, cloud_id)

    if detected_field_id:
        print(f"✅ PASS: Sprint field detected: {detected_field_id}")

        # Verify it's stored in project
        stored_field_id = jira.get('sprint_field_id')
        if stored_field_id == detected_field_id:
            print(f"✅ PASS: Field ID matches stored value: {stored_field_id}")
        else:
            print(f"⚠️  INFO: Stored field ID: {stored_field_id} (will be updated on next OAuth)")
    else:
        print(f"❌ FAIL: Could not detect sprint field")
        client.close()
        return False

    client.close()
    return True


async def test_sync_with_sprint_data():
    """Test 2: Verify sync fetches sprint data correctly"""
    print("\n" + "="*70)
    print("TEST 2: Sprint Data Sync Verification")
    print("="*70)

    client = AsyncIOMotorClient(os.environ['MONGODB_URI'])
    brain_db = client.get_database(os.environ.get('BRAIN_DB_NAME', 'brain-dev'))
    collection = brain_db['proj-6342-3297']

    # Check tasks in brain database
    tasks = await collection.find({"entity_type": "work_item"}).to_list(length=None)

    print(f"\n📋 Checking {len(tasks)} tasks in brain database:")

    tasks_with_sprint = []
    tasks_without_sprint = []

    for task in tasks:
        key = task.get('external_id')
        sprint = task.get('sprint')
        sprint_state = task.get('sprint_state')

        if sprint:
            tasks_with_sprint.append(task)
            print(f"   ✅ {key}: Sprint='{sprint}', State='{sprint_state}'")
        else:
            tasks_without_sprint.append(task)
            print(f"   ⚪ {key}: No sprint (in Backlog)")

    print(f"\n📊 Results:")
    print(f"   Tasks with sprint: {len(tasks_with_sprint)}")
    print(f"   Tasks without sprint: {len(tasks_without_sprint)}")

    # Expected: TT-3 and TT-5 should have sprint data
    expected_sprint_tasks = {'TT-3', 'TT-5'}
    actual_sprint_tasks = {t['external_id'] for t in tasks_with_sprint}

    if expected_sprint_tasks.issubset(actual_sprint_tasks):
        print(f"✅ PASS: Expected tasks (TT-3, TT-5) have sprint data")
    else:
        print(f"❌ FAIL: Missing sprint data for: {expected_sprint_tasks - actual_sprint_tasks}")
        client.close()
        return False

    # Verify sprint state
    for task in tasks_with_sprint:
        if task['sprint_state'] == 'active':
            print(f"✅ PASS: {task['external_id']} has sprint_state='active'")
        else:
            print(f"❌ FAIL: {task['external_id']} has wrong sprint_state: {task['sprint_state']}")

    client.close()
    return True


async def test_fallback_mechanism():
    """Test 4: Fallback to common field IDs"""
    print("\n" + "="*70)
    print("TEST 4: Fallback Mechanism Test")
    print("="*70)

    from app.integrations.jira.normalizer import JiraNormalizer

    print(f"\n🔍 Testing normalizer fallback logic...")

    # Simulate Jira task response with different field IDs
    test_cases = [
        {
            "name": "Standard sprint field",
            "fields": {
                "sprint": [{"id": 1, "name": "Sprint 1", "state": "active"}]
            },
            "expected": "Sprint 1"
        },
        {
            "name": "customfield_10020 (detected)",
            "fields": {
                "customfield_10020": [{"id": 2, "name": "Sprint 2", "state": "active"}]
            },
            "expected": "Sprint 2"
        },
        {
            "name": "customfield_10001 (alternative)",
            "fields": {
                "customfield_10001": [{"id": 3, "name": "Sprint 3", "state": "future"}]
            },
            "expected": "Sprint 3"
        },
        {
            "name": "No sprint field",
            "fields": {},
            "expected": None
        }
    ]

    normalizer = JiraNormalizer(cloud_id="test", site_url="https://test.atlassian.net")

    all_passed = True
    for test_case in test_cases:
        # Create mock task
        mock_task = {
            "key": "TEST-1",
            "fields": {
                "summary": "Test task",
                "status": {"name": "To Do"},
                "issuetype": {"name": "Task"},
                **test_case["fields"]
            }
        }

        try:
            normalized = normalizer.normalize_task(mock_task)
            actual_sprint = normalized.get('sprint')
            expected_sprint = test_case['expected']

            if actual_sprint == expected_sprint:
                print(f"✅ PASS: {test_case['name']} → {actual_sprint or 'None'}")
            else:
                print(f"❌ FAIL: {test_case['name']} → Expected: {expected_sprint}, Got: {actual_sprint}")
                all_passed = False
        except Exception as e:
            print(f"❌ FAIL: {test_case['name']} → Error: {str(e)}")
            all_passed = False

    if all_passed:
        print(f"\n✅ PASS: All fallback tests passed")
    else:
        print(f"\n❌ FAIL: Some fallback tests failed")

    return all_passed


async def run_all_tests():
    """Run all tests"""
    print("\n" + "🎯 " + "="*66 + " 🎯")
    print("     SPRINT FIELD AUTO-DETECTION - COMPREHENSIVE TEST SUITE")
    print("🎯 " + "="*66 + " 🎯\n")

    results = {}

    # Test 1: Detection
    try:
        results['detection'] = await test_sprint_field_detection()
    except Exception as e:
        print(f"❌ TEST 1 FAILED: {str(e)}")
        results['detection'] = False

    # Test 2: Sync
    try:
        results['sync'] = await test_sync_with_sprint_data()
    except Exception as e:
        print(f"❌ TEST 2 FAILED: {str(e)}")
        results['sync'] = False

    # Test 3: Fallback
    try:
        results['fallback'] = await test_fallback_mechanism()
    except Exception as e:
        print(f"❌ TEST 3 FAILED: {str(e)}")
        results['fallback'] = False

    # Summary
    print("\n" + "="*70)
    print("FINAL RESULTS")
    print("="*70)

    total_tests = len(results)
    passed_tests = sum(1 for result in results.values() if result)

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name.upper()}")

    print(f"\n📊 Summary: {passed_tests}/{total_tests} tests passed")

    if passed_tests == total_tests:
        print("\n🎉 🎉 🎉  ALL TESTS PASSED!  🎉 🎉 🎉")
        print("\nSprint auto-detection is working correctly!")
    else:
        print(f"\n⚠️  {total_tests - passed_tests} test(s) failed")

    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
