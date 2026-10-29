// Downloads the three approved Asset Store packs and writes an inventory.
// Does not place props on the Onward map. Shaders stay custom-content PP_ only.
#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace MurciaGenerator
{
    [InitializeOnLoad]
    public static class AssetStorePropsImporter
    {
        private const string LogPrefix = "[AssetStoreProps]";
        private const string RequestName = "asset_store_import.request";
        private const string DestRoot = "Assets/ThirdParty/AssetStore";
        private static readonly string[] ProductIds = { "327313", "71042", "251403" };

        private static bool s_running;

        static AssetStorePropsImporter()
        {
            EditorApplication.delayCall += TryRunFromRequest;
        }

        [MenuItem("Murcia/Import approved Asset Store props")]
        public static void ImportFromMenu()
        {
            StartImport();
        }

        private static void TryRunFromRequest()
        {
            string request = FindRequestPath();
            if (string.IsNullOrEmpty(request) || !File.Exists(request))
                return;
            try
            {
                File.Move(request, request + ".started");
            }
            catch (IOException)
            {
                return;
            }
            StartImport();
        }

        private static string FindRequestPath()
        {
            string fallback = Path.Combine(Application.dataPath, "OnwardGenerator/Editor/" + RequestName);
            if (File.Exists(fallback))
                return fallback;
            string[] guids = AssetDatabase.FindAssets("asset_store_import");
            foreach (string guid in guids)
            {
                string path = AssetDatabase.GUIDToAssetPath(guid);
                if (path.EndsWith(RequestName, StringComparison.Ordinal))
                    return Path.GetFullPath(path);
            }
            return "";
        }

        private static void StartImport()
        {
            if (s_running)
            {
                Debug.LogWarning(LogPrefix + " already running");
                return;
            }
            s_running = true;
            DownloadNext(new Queue<string>(ProductIds), new List<ImportedPack>());
        }

        private static void DownloadNext(Queue<string> remaining, List<ImportedPack> done)
        {
            if (remaining.Count == 0)
            {
                WriteInventory(done);
                s_running = false;
                return;
            }

            string productId = remaining.Dequeue();
            string local = FindLocalPackage(productId);
            if (!string.IsNullOrEmpty(local))
            {
                Debug.Log(LogPrefix + " using local package " + local);
                ImportOne(productId, local, remaining, done);
                return;
            }

            Debug.Log(LogPrefix + " downloading " + productId);
            object op;
            Type opType;
            if (!TryStartDownload(productId, out op, out opType))
            {
                Debug.LogError(LogPrefix + " cannot start download for " + productId +
                               ". Open Package Manager > My Assets, add the free pack, then Murcia/Import approved Asset Store props.");
                DownloadNext(remaining, done);
                return;
            }

            EditorApplication.CallbackFunction tick = null;
            tick = () =>
            {
                string state = Convert.ToString(opType.GetProperty("state").GetValue(op));
                if (state == "Connecting" || state == "Downloading" || state == "Decrypting"
                    || state == "DownloadRequested" || state == "ResumeRequested")
                    return;
                EditorApplication.update -= tick;
                string packagePath = opType.GetProperty("packageNewPath").GetValue(op) as string;
                if (state != "Completed" || string.IsNullOrEmpty(packagePath) || !File.Exists(packagePath))
                {
                    Debug.LogError(LogPrefix + " download " + productId + " state=" + state +
                                   ". The Unity account in this Editor must own the free pack (My Assets).");
                    DownloadNext(remaining, done);
                    return;
                }
                ImportOne(productId, packagePath, remaining, done);
            };
            EditorApplication.update += tick;
        }

        // Same defaults as tools/unity/lib.sh, which exports ASSET_STORE_CACHE before Unity runs.
        private static string AssetStoreCache()
        {
            string overridden = Environment.GetEnvironmentVariable("ASSET_STORE_CACHE");
            if (!string.IsNullOrEmpty(overridden))
                return overridden;
            string home = Environment.GetFolderPath(Environment.SpecialFolder.Personal);
            if (Application.platform == RuntimePlatform.LinuxEditor)
                return Path.Combine(home, ".local/share/unity3d/Asset Store-5.x");
            return Path.Combine(home, "Library/Unity/Asset Store-5.x");
        }

        private static string FindLocalPackage(string productId)
        {
            string cache = AssetStoreCache();
            if (!Directory.Exists(cache))
                return "";
            string[] hints;
            switch (productId)
            {
                case "327313":
                    hints = new[] { "mobile optimize", "low poly cars" };
                    break;
                case "71042":
                    hints = new[] { "european city" };
                    break;
                case "251403":
                    hints = new[] { "stylized trees", "cartoon" };
                    break;
                default:
                    return "";
            }
            foreach (string file in Directory.GetFiles(cache, "*.unitypackage", SearchOption.AllDirectories))
            {
                string name = Path.GetFileNameWithoutExtension(file).ToLowerInvariant();
                foreach (string hint in hints)
                {
                    if (name.Contains(hint))
                        return file;
                }
            }
            return "";
        }

        private static bool TryStartDownload(string productId, out object op, out Type opType)
        {
            op = null;
            opType = Type.GetType("UnityEditor.PackageManager.UI.Internal.AssetStoreDownloadOperation, UnityEditor.PackageManagerUIModule");
            Type containerType = Type.GetType("UnityEditor.PackageManager.UI.Internal.ServicesContainer, UnityEditor.PackageManagerUIModule");
            Type utilsType = Type.GetType("UnityEditor.PackageManager.UI.Internal.AssetStoreUtils, UnityEditor.PackageManagerUIModule");
            Type restType = Type.GetType("UnityEditor.PackageManager.UI.Internal.AssetStoreRestAPI, UnityEditor.PackageManagerUIModule");
            if (opType == null || containerType == null || utilsType == null || restType == null)
                return false;

            object container = GetSingleton(containerType);
            if (container == null)
                return false;

            const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            MethodInfo resolve = null;
            foreach (MethodInfo method in containerType.GetMethods(flags))
            {
                if (method.Name == "Resolve" && method.IsGenericMethodDefinition && method.GetParameters().Length == 0)
                {
                    resolve = method;
                    break;
                }
            }
            if (resolve == null)
                return false;

            object utils = resolve.MakeGenericMethod(utilsType).Invoke(container, null);
            object rest = resolve.MakeGenericMethod(restType).Invoke(container, null);
            ConstructorInfo ctor = opType.GetConstructor(flags, null, new[] { utilsType, restType, typeof(string), typeof(string) }, null);
            if (utils == null || rest == null || ctor == null)
                return false;

            op = ctor.Invoke(new object[] { utils, rest, productId, null });
            MethodInfo download = opType.GetMethod("Download", flags, null, new[] { typeof(bool) }, null);
            if (download == null)
                return false;
            download.Invoke(op, new object[] { false });
            return true;
        }

        private static object GetSingleton(Type type)
        {
            Type current = type;
            while (current != null)
            {
                PropertyInfo instance = current.GetProperty("instance", BindingFlags.Public | BindingFlags.Static | BindingFlags.FlattenHierarchy);
                if (instance != null)
                    return instance.GetValue(null);
                current = current.BaseType;
            }
            return null;
        }

        private static void ImportOne(string productId, string packagePath, Queue<string> remaining, List<ImportedPack> done)
        {
            string dest = DestRoot + "/" + productId;
            EnsureFolder(dest);
            HashSet<string> before = new HashSet<string>(AssetDatabase.GetAllAssetPaths());

            AssetDatabase.importPackageCompleted += OnCompleted;
            AssetDatabase.importPackageFailed += OnFailed;
            AssetDatabase.ImportPackage(packagePath, false);

            void OnCompleted(string packageName)
            {
                AssetDatabase.importPackageCompleted -= OnCompleted;
                AssetDatabase.importPackageFailed -= OnFailed;
                AssetDatabase.Refresh();
                List<string> imported = AssetDatabase.GetAllAssetPaths()
                    .Where(p => p.StartsWith("Assets/", StringComparison.Ordinal) && !before.Contains(p))
                    .ToList();
                List<string> moved = new List<string>();
                foreach (string asset in imported)
                {
                    if (asset.StartsWith(dest + "/", StringComparison.Ordinal) || asset.EndsWith(".meta", StringComparison.Ordinal))
                    {
                        moved.Add(asset);
                        continue;
                    }
                    string target = AssetDatabase.GenerateUniqueAssetPath(dest + "/" + Path.GetFileName(asset));
                    string err = AssetDatabase.MoveAsset(asset, target);
                    moved.Add(string.IsNullOrEmpty(err) ? target : asset);
                }
                done.Add(new ImportedPack { productId = productId, packagePath = packagePath, assets = moved });
                Debug.Log(LogPrefix + " imported " + productId + " assets=" + moved.Count);
                DownloadNext(remaining, done);
            }

            void OnFailed(string packageName, string errorMessage)
            {
                AssetDatabase.importPackageCompleted -= OnCompleted;
                AssetDatabase.importPackageFailed -= OnFailed;
                Debug.LogError(LogPrefix + " import failed " + productId + ": " + errorMessage);
                DownloadNext(remaining, done);
            }
        }

        private static void WriteInventory(List<ImportedPack> done)
        {
            EnsureFolder(DestRoot);
            var assets = new List<Dictionary<string, string>>();
            foreach (ImportedPack pack in done)
            {
                foreach (string path in pack.assets)
                    assets.Add(new Dictionary<string, string> { { "pack_id", pack.productId }, { "path", path } });
            }
            string project = Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            string json = BuildInventoryJson(project, done, assets);
            string jsonPath = Path.Combine(Application.dataPath, "ThirdParty/AssetStore/import_inventory.json");
            Directory.CreateDirectory(Path.GetDirectoryName(jsonPath));
            File.WriteAllText(jsonPath, json);
            AssetDatabase.Refresh();
            Debug.Log(LogPrefix + " OK inventory " + jsonPath + " assets=" + assets.Count);
        }

        private static string BuildInventoryJson(string project, List<ImportedPack> done, List<Dictionary<string, string>> assets)
        {
            var sb = new System.Text.StringBuilder();
            sb.Append("{\"schema\":\"onward_generator.asset_store_inventory\",\"schema_version\":\"0.1.0\",");
            sb.Append("\"unity_project\":").Append(Quote(project)).Append(",");
            sb.Append("\"imported_at\":").Append(Quote(DateTime.UtcNow.ToString("o"))).Append(",");
            sb.Append("\"packs\":[");
            for (int i = 0; i < done.Count; i++)
            {
                if (i > 0) sb.Append(",");
                sb.Append(Quote(done[i].productId));
            }
            sb.Append("],\"assets\":[");
            for (int i = 0; i < assets.Count; i++)
            {
                if (i > 0) sb.Append(",");
                sb.Append("{\"pack_id\":").Append(Quote(assets[i]["pack_id"]));
                sb.Append(",\"path\":").Append(Quote(assets[i]["path"])).Append("}");
            }
            sb.Append("]}");
            return sb.ToString();
        }

        private static string Quote(string value)
        {
            return "\"" + (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
        }

        private static void EnsureFolder(string assetPath)
        {
            if (AssetDatabase.IsValidFolder(assetPath))
                return;
            string parent = Path.GetDirectoryName(assetPath);
            if (!string.IsNullOrEmpty(parent))
                parent = parent.Replace("\\", "/");
            string name = Path.GetFileName(assetPath);
            if (!string.IsNullOrEmpty(parent) && parent != "Assets")
                EnsureFolder(parent);
            AssetDatabase.CreateFolder(string.IsNullOrEmpty(parent) ? "Assets" : parent, name);
        }

        private class ImportedPack
        {
            public string productId;
            public string packagePath;
            public List<string> assets;
        }
    }
}
#endif
