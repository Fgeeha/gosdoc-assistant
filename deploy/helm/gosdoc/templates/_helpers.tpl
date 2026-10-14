{{- define "gosdoc.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "gosdoc.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "gosdoc.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "gosdoc.image" -}}
{{- $tag := .root.Values.image.tag | default .root.Chart.AppVersion -}}
{{- with .root.Values.image.registry }}{{ . }}/{{ end }}gosdoc-{{ .name }}:{{ $tag }}
{{- end -}}

{{- define "gosdoc.secretName" -}}
{{ .Values.existingSecret | default (printf "%s-secret" (include "gosdoc.fullname" .)) }}
{{- end -}}

{{- define "gosdoc.env" -}}
envFrom:
  - configMapRef: {name: {{ include "gosdoc.fullname" . }}-config}
  - secretRef: {name: {{ include "gosdoc.secretName" . }}}
{{- if or .Values.caBundle.secretName .Values.caBundle.configMapName }}
env:
  - name: CA_BUNDLE
    value: {{ printf "%s/%s" .Values.caBundle.mountPath .Values.caBundle.key }}
{{- end }}
{{- end -}}

{{- define "gosdoc.mounts" -}}
volumeMounts:
  - {name: tmp, mountPath: /tmp}
{{- if or .Values.caBundle.secretName .Values.caBundle.configMapName }}
  - {name: ca, mountPath: {{ .Values.caBundle.mountPath }}, readOnly: true}
{{- end }}
{{- end -}}

{{- define "gosdoc.volumes" -}}
- name: tmp
  emptyDir: {}
{{- if .Values.caBundle.secretName }}
- name: ca
  secret: {secretName: {{ .Values.caBundle.secretName }}}
{{- else if .Values.caBundle.configMapName }}
- name: ca
  configMap: {name: {{ .Values.caBundle.configMapName }}}
{{- end }}
{{- end -}}

{{- define "gosdoc.pod" -}}
automountServiceAccountToken: false
securityContext: {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.image.pullSecrets }}
imagePullSecrets: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* initContainer: дождаться зависимостей (и миграций) до старта основного контейнера. */}}
{{- define "gosdoc.waitInit" -}}
- name: wait-deps
  image: {{ include "gosdoc.image" (dict "root" .root "name" "api") }}
  imagePullPolicy: {{ .root.Values.image.pullPolicy }}
  command: ["sh", "-c", "gosdoc wait-deps --only {{ .deps }} --timeout {{ .root.Values.waitDeps.timeoutSeconds }}{{ if .migrations }} && gosdoc wait-migrations --timeout 900{{ end }}"]
  {{- include "gosdoc.env" .root | nindent 2 }}
  {{- include "gosdoc.mounts" .root | nindent 2 }}
  securityContext: {{- toYaml .root.Values.containerSecurityContext | nindent 4 }}
  resources: {requests: {cpu: 50m, memory: 128Mi}, limits: {cpu: 500m, memory: 256Mi}}
{{- end -}}
