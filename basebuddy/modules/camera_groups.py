"""
Camera Groups Management - Organize cameras into named groups
"""
import json
import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

# Store groups in a JSON file for simplicity
GROUPS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'camera_groups.json')

# Available icons for groups (Material Icons)
AVAILABLE_ICONS = [
    'home', 'business', 'storefront', 'warehouse', 'garage',
    'yard', 'deck', 'fence', 'door_front', 'door_back',
    'meeting_room', 'bedroom_parent', 'living', 'kitchen', 'bathroom',
    'stairs', 'elevator', 'local_parking', 'directions_car', 'two_wheeler',
    'pets', 'child_care', 'elderly', 'security', 'videocam',
    'nature', 'park', 'forest', 'grass', 'eco',
    'wb_sunny', 'nights_stay', 'cloud', 'water', 'pool',
    'fitness_center', 'sports', 'workspace_premium', 'factory', 'agriculture',
]


@dataclass
class CameraGroup:
    """A group of cameras"""
    id: str  # Unique identifier
    name: str  # Display name
    icon: str = 'folder'  # Material icon name
    camera_ids: List[int] = None  # List of camera IDs in this group
    color: str = '#1a73e8'  # Accent color
    order: int = 0  # Display order
    
    def __post_init__(self):
        if self.camera_ids is None:
            self.camera_ids = []


def load_groups() -> List[CameraGroup]:
    """Load all camera groups from file"""
    try:
        if os.path.exists(GROUPS_FILE):
            with open(GROUPS_FILE, 'r') as f:
                data = json.load(f)
                return [CameraGroup(**g) for g in data.get('groups', [])]
    except Exception as e:
        logger.error(f"Error loading camera groups: {e}")
    return []


def save_groups(groups: List[CameraGroup]):
    """Save camera groups to file"""
    try:
        data = {
            'version': 1,
            'groups': [asdict(g) for g in groups]
        }
        with open(GROUPS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Error saving camera groups: {e}")
        return False


def get_group(group_id: str) -> Optional[CameraGroup]:
    """Get a specific group by ID"""
    groups = load_groups()
    for g in groups:
        if g.id == group_id:
            return g
    return None


def create_group(name: str, icon: str = 'folder', camera_ids: List[int] = None, color: str = '#1a73e8') -> CameraGroup:
    """Create a new camera group"""
    import uuid
    groups = load_groups()
    
    new_group = CameraGroup(
        id=str(uuid.uuid4())[:8],
        name=name,
        icon=icon,
        camera_ids=camera_ids or [],
        color=color,
        order=len(groups)
    )
    
    groups.append(new_group)
    save_groups(groups)
    return new_group


def update_group(group_id: str, **kwargs) -> Optional[CameraGroup]:
    """Update an existing group"""
    groups = load_groups()
    for i, g in enumerate(groups):
        if g.id == group_id:
            for key, value in kwargs.items():
                if hasattr(g, key):
                    setattr(g, key, value)
            save_groups(groups)
            return g
    return None


def delete_group(group_id: str) -> bool:
    """Delete a camera group"""
    groups = load_groups()
    groups = [g for g in groups if g.id != group_id]
    return save_groups(groups)


def add_camera_to_group(group_id: str, camera_id: int) -> bool:
    """Add a camera to a group"""
    groups = load_groups()
    for g in groups:
        if g.id == group_id:
            if camera_id not in g.camera_ids:
                g.camera_ids.append(camera_id)
                return save_groups(groups)
            return True
    return False


def remove_camera_from_group(group_id: str, camera_id: int) -> bool:
    """Remove a camera from a group"""
    groups = load_groups()
    for g in groups:
        if g.id == group_id:
            if camera_id in g.camera_ids:
                g.camera_ids.remove(camera_id)
                return save_groups(groups)
            return True
    return False


def get_cameras_in_group(group_id: str) -> List[int]:
    """Get list of camera IDs in a group"""
    group = get_group(group_id)
    if group:
        return group.camera_ids
    return []


def to_dict(group: CameraGroup) -> Dict[str, Any]:
    """Convert group to dictionary for JSON"""
    return asdict(group)









