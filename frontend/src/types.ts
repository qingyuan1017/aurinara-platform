export interface User {
  id: string
  issuer: string
  subject: string
  email: string | null
  display_name: string | null
  status: 'active' | 'inactive'
  revision: number
}

export interface Study {
  id: string
  code: string
  display_name: string
  sponsor: string | null
  status: 'active' | 'archived'
  revision: number
  created_by: string
  created_at: string
  updated_at: string
}

export interface Site {
  id: string
  study_id: string
  name: string
  country: string | null
  state: string | null
  city: string | null
  status: 'active' | 'inactive'
  created_at: string
  updated_at: string
}

export interface Membership {
  id: string
  study_id: string
  user_id: string
  role_id: string
  role_name: string
  product: string
  user_display_name: string | null
  user_email: string | null
  site_id: string | null
  assigned_at: string
}

export interface Role {
  id: string
  key: string
  product: string
  name: string
  description: string | null
  scope_level: 'system' | 'study' | 'site'
  assignable: boolean
  built_in: boolean
  permission_policy: 'explicit' | 'all' | 'all_scoped' | 'read_scoped'
  status: 'active' | 'inactive'
  source_role_id: string | null
  revision: number
  permission_codes: string[]
  updated_at: string
}

export interface Permission {
  code: string
  product: string
  description: string | null
  module: string | null
  allowed_scope_levels: Array<'system' | 'study' | 'site'>
  status: 'active' | 'inactive'
}

export interface RoleChange {
  id: string
  role_id: string
  actor_id: string
  action: string
  reason: string
  previous_value: Record<string, unknown> | null
  new_value: Record<string, unknown>
  policy_version: number
  created_at: string
}

export interface SystemAssignment {
  id: string
  user_id: string
  role_id: string
  role_name: string
  product: string
  assigned_at: string
}

export interface Decision {
  allowed: boolean
  decision_id: string
  policy_version: number
  expires_at: string
  reason_code: string
}

export interface StoredObject {
  id: string
  study_id: string
  owner_product: string
  classification: string
  sha256: string | null
  size_bytes: number | null
  media_type: string
  status: string
  retention_until: string | null
  retention_state: string
  created_by: string
  created_at: string
  finalized_at: string | null
}

export interface RepositoryNode {
  id: string
  study_id: string
  parent_id: string | null
  kind: 'folder' | 'file'
  display_name: string
  source_product: string
  module: string | null
  classification: string
  required_read_action: string
  required_write_action: string
  site_id: string | null
  source_resource_type: string
  source_resource_id: string | null
  source_status: string
  retention_metadata: Record<string, unknown>
  lifecycle_status: 'active' | 'archived'
  revision: number
  current_version_number: number | null
  created_by: string
  updated_by: string
  created_at: string
  updated_at: string
  archived_at: string | null
}

export interface RepositoryPage {
  items: RepositoryNode[]
  next_cursor: string | null
}

export interface RepositoryVersion {
  id: string
  node_id: string
  version_number: number
  object_id: string
  object_reference_id: string | null
  sha256: string
  size_bytes: number
  media_type: string
  created_by: string
  created_at: string
}

export interface ProblemPayload {
  error?: {
    code?: string
    message?: string
    details?: unknown
    request_id?: string
    correlation_id?: string
  }
}
