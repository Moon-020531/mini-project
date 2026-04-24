import React from 'react';
import { Heart } from 'lucide-react';

export default function Footer() {
  return (
    <footer className="app-footer">
      <div className="footer-content">
        <p className="footer-copyright">
          © 2026 Anti-Gravity. All rights reserved.
        </p>
        <div className="footer-message">
          Made with <Heart size={14} className="heart-icon" /> for your healthy posture
        </div>
      </div>
    </footer>
  );
}
