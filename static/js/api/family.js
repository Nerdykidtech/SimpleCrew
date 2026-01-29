/**
 * @file family.js
 * @description API layer for family member and user profile management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (familyDataStore, cardColors)
 */

/**
 * Load family members list
 */
function loadFamily() {
    fetch('/api/family').then(res => res.json()).then(data => {
        if(data.error) return;
        familyDataStore = [...data.children, ...data.parents];
        const container = document.getElementById('family-content');
        let html = '';
        if(data.children.length > 0) {
            html += `<div class="family-section-title">Children</div><div class="family-grid">`;
            data.children.forEach((child, index) => {
                const stripColor = cardColors[child.color] || '#CCC';
                html += `<div class="family-card" onclick="openFamilyDetail('child', ${index})"><div class="color-strip" style="background:${stripColor}"></div><img src="${child.image}" class="profile-img"><div class="family-name">${child.name}</div><div class="family-role">Child</div><div class="family-balance">${fmt(child.balance)}</div></div>`;
            });
            html += `</div>`;
        }
        if(data.parents.length > 0) {
            html += `<div class="family-section-title">Parents</div><div class="family-grid">`;
            data.parents.forEach((parent, index) => {
                const stripColor = cardColors[parent.color] || '#CCC';
                html += `<div class="family-card" style="cursor:default"><div class="color-strip" style="background:${stripColor}"></div><img src="${parent.image}" class="profile-img"><div class="family-name">${parent.name}</div><div class="family-role">Parent</div></div>`;
            });
            html += `</div>`;
        }
        container.innerHTML = html;
    });
}

/**
 * Load user profile information
 */
function loadUserProfile() {
    fetch('/api/user')
        .then(res => res.json())
        .then(data => {
            if (data.error) return;

            const first = data.firstName || "";
            const last = data.lastName || "";
            const imgUrl = data.imageUrl;

            // Update Name
            const fullName = `${first} ${last}`;
            const nameEl = document.getElementById('user-name');
            if(nameEl) nameEl.innerText = fullName;

            const avatarEl = document.getElementById('user-avatar');
            if (!avatarEl) return;

            if (imgUrl) {
                // 1. If Image URL exists, inject an IMG tag
                // We set background to transparent to hide the default orange color
                avatarEl.style.background = 'transparent';
                avatarEl.innerHTML = `<img src="${imgUrl}" style="width:100%; height:100%; border-radius:50%; object-fit:cover; display:block;">`;
            } else {
                // 2. Fallback to Initials if no image found
                let initials = "";
                if (first.length > 0) initials += first[0];
                if (last.length > 0) initials += last[0];

                // Reset style to default orange background (defined in CSS)
                avatarEl.style.background = '';
                avatarEl.innerText = initials.toUpperCase();
            }
        })
        .catch(err => console.error("Failed to load user profile", err));
}

/**
 * Load and initialize Intercom widget
 */
function loadIntercom() {
    fetch('/api/intercom')
        .then(res => res.json())
        .then(data => {
            if(data.error) {
                console.log("Intercom skipped: " + data.error);
                return;
            }

            const userData = data.user_data;

            // Prepare Settings
            // Inside your loadIntercom() function...

            window.intercomSettings = {
                api_base: "https://api-iam.intercom.io",
                app_id: "c7bal0a1",

                user_id: userData.user_id,
                intercom_user_jwt: userData.intercom_user_jwt,

                // NEW: Hide the bubble automatically if on mobile
                hide_default_launcher: window.innerWidth <= 768,

                launcher_logo_url: "https://media.licdn.com/dms/image/v2/D560BAQEroTqp4W9tBg/company-logo_200_200/company-logo_200_200/0/1686260003377/trycrew_logo?e=2147483647&v=beta&t=AFyDbpJ8X-2MB86GkQo9MmPMZGLuUp-FMu-BDHH5hvM"
            };

            // Initialize the Messenger
            if (window.Intercom) {
                window.Intercom('boot', window.intercomSettings);
            }
        })
        .catch(err => console.error("Intercom fetch failed", err));
}

/**
 * Open Intercom help widget
 */
function openIntercom() {
    if (window.Intercom) {
        window.Intercom('show');
    } else {
        console.log('Intercom not available');
    }
}
