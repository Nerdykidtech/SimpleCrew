/**
 * @file family.js
 * @description API layer for family member and user profile management
 * @requires utils/formatting.js (fmt function)
 * @requires state.js (familyDataStore, cardColors)
 */

// Store currently selected child for detail view
let selectedChildData = null;

/**
 * Load family members list (kids only)
 */
function loadFamily() {
    fetch('/api/family').then(res => res.json()).then(data => {
        if(data.error) return;
        familyDataStore = data.children;
        const container = document.getElementById('family-content');

        if(data.children.length === 0) {
            container.innerHTML = `
                <div class="family-empty">
                    <div class="family-empty-icon">👨‍👩‍👧‍👦</div>
                    <div class="family-empty-title">No Kids Added Yet</div>
                    <div class="family-empty-text">Add children to your Crew account to manage their spending and cards.</div>
                </div>
            `;
            return;
        }

        let html = '<div class="family-grid">';
        data.children.forEach((child, index) => {
            const stripColor = cardColors[child.color] || '#CCC';
            const age = calculateAge(child.dob);
            html += `
                <div class="family-card" onclick="openChildDetail(${index})">
                    <div class="color-strip" style="background:${stripColor}"></div>
                    <img src="${child.image}" class="profile-img">
                    <div class="family-name">${child.name}</div>
                    <div class="family-age">${age} years old</div>
                    <div class="family-balance">${fmt(child.balance)}</div>
                    <div class="family-allowance">${child.allowance}</div>
                </div>
            `;
        });
        html += '</div>';
        container.innerHTML = html;
    });
}

/**
 * Calculate age from date of birth
 */
function calculateAge(dob) {
    if (!dob) return '?';
    const birthDate = new Date(dob);
    const today = new Date();
    let age = today.getFullYear() - birthDate.getFullYear();
    const monthDiff = today.getMonth() - birthDate.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) {
        age--;
    }
    return age;
}

/**
 * Open child detail view
 */
function openChildDetail(index) {
    const child = familyDataStore[index];
    if (!child) return;

    selectedChildData = child;
    const container = document.getElementById('family-content');
    const stripColor = cardColors[child.color] || '#CCC';
    const age = calculateAge(child.dob);

    container.innerHTML = `
        <div class="child-detail-view">
            <div class="child-detail-header">
                <button class="back-btn" onclick="loadFamily()">← Back</button>
            </div>

            <div class="child-profile-card">
                <div class="child-profile-strip" style="background: ${stripColor}"></div>
                <div class="child-profile-content">
                    <img src="${child.image}" class="child-profile-img">
                    <div class="child-profile-info">
                        <div class="child-profile-name">${child.name}</div>
                        <div class="child-profile-age">${age} years old</div>
                    </div>
                    <div class="child-profile-balance">
                        <div class="child-balance-amount">${fmt(child.balance)}</div>
                        <div class="child-balance-label">Checking Balance</div>
                    </div>
                </div>
            </div>

            <div class="child-detail-tabs">
                <button class="child-tab active" onclick="switchChildTab('activity', this)">Activity</button>
                <button class="child-tab" onclick="switchChildTab('cards', this)">Cards</button>
                <button class="child-tab" onclick="switchChildTab('settings', this)">Settings</button>
            </div>

            <div class="child-detail-content" id="child-detail-content">
                <div style="text-align:center; padding:30px; color:var(--text-muted);">Loading...</div>
            </div>
        </div>
    `;

    // Load activity by default
    loadChildActivity(child.id);
}

/**
 * Switch between child detail tabs
 */
function switchChildTab(tab, btnEl) {
    // Update active tab button
    document.querySelectorAll('.child-tab').forEach(btn => btn.classList.remove('active'));
    if (btnEl) btnEl.classList.add('active');

    const content = document.getElementById('child-detail-content');
    content.innerHTML = '<div style="text-align:center; padding:30px; color:var(--text-muted);">Loading...</div>';

    switch(tab) {
        case 'activity':
            loadChildActivity(selectedChildData.id);
            break;
        case 'cards':
            loadChildCards(selectedChildData.id);
            break;
        case 'settings':
            loadChildSettings(selectedChildData);
            break;
    }
}

/**
 * Load child's recent activity
 */
function loadChildActivity(childId) {
    const content = document.getElementById('child-detail-content');

    // For now, show placeholder - we'll need to add an API endpoint for child transactions
    content.innerHTML = `
        <div class="child-activity-list">
            <div class="child-activity-empty">
                <div style="font-size: 32px; margin-bottom: 10px;">📋</div>
                <div>Activity coming soon</div>
            </div>
        </div>
    `;
}

/**
 * Load child's cards
 */
function loadChildCards(childId) {
    const content = document.getElementById('child-detail-content');

    fetch('/api/cards').then(res => res.json()).then(data => {
        if (data.error) {
            content.innerHTML = `<div style="color:red; padding:20px;">${data.error}</div>`;
            return;
        }

        // Filter virtual cards belonging to this child
        const childCards = (data.virtualCards || []).filter(card =>
            card.userId === childId && card.status === 'ACTIVATED'
        );

        if (childCards.length === 0) {
            content.innerHTML = `
                <div class="child-cards-empty">
                    <div style="font-size: 32px; margin-bottom: 10px;">💳</div>
                    <div>No active cards</div>
                </div>
            `;
            return;
        }

        let html = '<div class="child-cards-list">';
        childCards.forEach(card => {
            const bg = cardColors[card.color] || '#333';
            const statusClass = card.frozenStatus === 'FROZEN' ? 'frozen' : '';
            html += `
                <div class="child-card-item ${statusClass}">
                    <div class="child-card-visual" style="background: linear-gradient(135deg, ${bg} 0%, ${adjustColor(bg, -30)} 100%);">
                        <div class="child-card-chip"></div>
                        <div class="child-card-number">•••• ${card.last4}</div>
                    </div>
                    <div class="child-card-info">
                        <div class="child-card-name">${card.name}</div>
                        <div class="child-card-type">${card.type}${card.frozenStatus === 'FROZEN' ? ' • Frozen' : ''}</div>
                    </div>
                </div>
            `;
        });
        html += '</div>';
        content.innerHTML = html;
    });
}

/**
 * Load child's settings
 */
function loadChildSettings(child) {
    const content = document.getElementById('child-detail-content');
    const age = calculateAge(child.dob);

    content.innerHTML = `
        <div class="child-settings">
            <div class="child-setting-group">
                <div class="child-setting-label">Allowance</div>
                <div class="child-setting-value">${child.allowance}</div>
            </div>
            <div class="child-setting-group">
                <div class="child-setting-label">Age</div>
                <div class="child-setting-value">${age} years old</div>
            </div>
            <div class="child-setting-group">
                <div class="child-setting-label">Card Color</div>
                <div class="child-setting-value">
                    <span style="color:${cardColors[child.color] || '#999'}">●</span> ${child.color || 'Default'}
                </div>
            </div>
        </div>
    `;
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
