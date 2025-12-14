"""
ICS Subscription handler for read-only calendar feeds.

Simplified: Just fetches raw VCALENDAR text. Recurrence expansion is handled
by EventRepository using recurring_ical_events.
"""

import requests
from datetime import datetime
from typing import Optional
from dataclasses import dataclass
import pytz
import hashlib


@dataclass
class SubscriptionInfo:
    """Information about an ICS subscription."""
    id: str
    name: str
    url: str
    color: str = "#34a853"
    last_fetch: Optional[datetime] = None
    error: Optional[str] = None


class ICSSubscription:
    """
    Handler for ICS calendar subscriptions.
    
    Simplified to just fetch raw VCALENDAR text. The EventRepository
    handles parsing and recurrence expansion using recurring_ical_events.
    """
    
    def __init__(self, name: str, url: str, color: str = "#34a853"):
        """
        Initialize an ICS subscription.
        
        Args:
            name: Display name for the subscription
            url: URL to fetch the ICS file from
            color: Color to display events (hex format)
        """
        self.name = name
        self.url = url
        self.color = color
        self.id = self._generate_id(url)
        
        self._raw_data: Optional[str] = None
        self._last_fetch: Optional[datetime] = None
        self._error: Optional[str] = None
    
    @staticmethod
    def _generate_id(url: str) -> str:
        """Generate a unique ID from the URL."""
        return hashlib.md5(url.encode()).hexdigest()[:12]
    
    def fetch(self, timeout: int = 30) -> bool:
        """
        Fetch the ICS file from the URL.
        
        Args:
            timeout: Request timeout in seconds
        
        Returns:
            True if successful, False otherwise.
        """
        try:
            response = requests.get(
                self.url,
                timeout=timeout,
                headers={
                    'User-Agent': 'Kubux-Calendar/1.0',
                    'Accept': 'text/calendar'
                }
            )
            response.raise_for_status()
            
            # Ensure proper UTF-8 decoding
            response.encoding = 'utf-8'
            self._raw_data = response.text
            self._last_fetch = datetime.now(pytz.UTC)
            self._error = None
            
            return True
        
        except requests.RequestException as e:
            self._error = f"Network error: {e}"
            return False
        except Exception as e:
            self._error = f"Error: {e}"
            return False
    
    def get_ical_text(
        self,
        force_fetch: bool = False,
        cache_seconds: int = 300
    ) -> Optional[str]:
        """
        Get the raw VCALENDAR text.
        
        Args:
            force_fetch: If True, always fetch from URL
            cache_seconds: How long to use cached data (default 5 minutes)
        
        Returns:
            Raw VCALENDAR text, or None if fetch failed.
        """
        # Check if we need to fetch
        should_fetch = (
            force_fetch or
            self._raw_data is None or
            self._last_fetch is None or
            (datetime.now(pytz.UTC) - self._last_fetch).total_seconds() > cache_seconds
        )
        
        if should_fetch:
            self.fetch()
        
        return self._raw_data
    
    @property
    def raw_data(self) -> Optional[str]:
        """Get the cached raw VCALENDAR text."""
        return self._raw_data
    
    @property
    def last_fetch(self) -> Optional[datetime]:
        """Get the last fetch time."""
        return self._last_fetch
    
    @property
    def error(self) -> Optional[str]:
        """Get the last error message."""
        return self._error
    
    def get_info(self) -> SubscriptionInfo:
        """Get information about this subscription."""
        return SubscriptionInfo(
            id=self.id,
            name=self.name,
            url=self.url,
            color=self.color,
            last_fetch=self._last_fetch,
            error=self._error
        )


class ICSSubscriptionManager:
    """Manager for multiple ICS subscriptions."""
    
    def __init__(self):
        self._subscriptions: dict[str, ICSSubscription] = {}
    
    def add_subscription(self, name: str, url: str, color: str = "#34a853") -> ICSSubscription:
        """Add a new subscription."""
        sub = ICSSubscription(name=name, url=url, color=color)
        self._subscriptions[sub.id] = sub
        return sub
    
    def remove_subscription(self, subscription_id: str) -> bool:
        """Remove a subscription."""
        if subscription_id in self._subscriptions:
            del self._subscriptions[subscription_id]
            return True
        return False
    
    def get_subscription(self, subscription_id: str) -> Optional[ICSSubscription]:
        """Get a subscription by ID."""
        return self._subscriptions.get(subscription_id)
    
    def get_all_subscriptions(self) -> list[ICSSubscription]:
        """Get all subscriptions."""
        return list(self._subscriptions.values())
    
    def fetch_all(self) -> dict[str, bool]:
        """
        Fetch all subscriptions.
        
        Returns:
            Dict mapping subscription ID to success status.
        """
        results = {}
        for sub_id, sub in self._subscriptions.items():
            results[sub_id] = sub.fetch()
        return results
    
    def get_all_ical_texts(self, force_fetch: bool = False) -> dict[str, Optional[str]]:
        """
        Get raw VCALENDAR text from all subscriptions.
        
        Args:
            force_fetch: If True, fetch fresh data from all URLs
        
        Returns:
            Dict mapping subscription ID to VCALENDAR text (or None if failed).
        """
        results = {}
        for sub_id, sub in self._subscriptions.items():
            results[sub_id] = sub.get_ical_text(force_fetch=force_fetch)
        return results
