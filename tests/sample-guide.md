# Lab: Create a YouTube Bookmark App in Okta

## Step 1: Create the Bookmark App

1. In the Okta Admin Console, navigate to **Applications > Applications**
2. Click **Browse App Catalog**
3. Search for **Bookmark App** and click **Add Integration**
4. Configure the app:
   - **Application label**: `YouTube`
   - **URL**: `https://www.youtube.com`
5. Click **Done**

[SCREENSHOT: YouTube bookmark app general settings page after creation]

## Step 2: Add an App Logo

1. On the app's **General** tab, click the Okta logo placeholder
2. Upload a YouTube logo image
3. Click **Save**

[SCREENSHOT: App general tab showing the YouTube logo]

## Step 3: Assign a User

1. Click the **Assignments** tab
2. Click **Assign > Assign to People**
3. Find **joe.vanhorn@okta.com** and click **Assign**
4. Click **Save and Go Back**, then **Done**

[SCREENSHOT: Assignments tab showing joe.vanhorn assigned]

## Verification

The YouTube bookmark app should now appear in joe.vanhorn's Okta dashboard. When clicked, it opens youtube.com in a new tab.
