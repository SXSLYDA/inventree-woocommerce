<?php
/**
 * Quote requests: the request form, My Account > Quotes, the sample label,
 * and the rules that keep quote-only products out of normal checkout.
 *
 * Customers must be logged in. Each request has one or more items; a custom
 * harness (or a copy of one) needs a layout drawing / photos, a sample, or a
 * call to discuss. The request goes straight to InvenTree; if InvenTree can't
 * be reached it is queued here and retried, so nothing the customer sent is lost.
 *
 * @package InvenTreeShop
 */

defined( 'ABSPATH' ) || exit;

class InvenTree_Shop_Quotes {

	const ENDPOINT    = 'quotes';
	const FILE_TYPES  = array( 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'dxf', 'dwg', 'xlsx', 'xls', 'csv', 'docx', 'doc', 'txt' );
	const KINDS       = array( 'custom', 'copy', 'component' );
	const QUEUE_OPT   = 'inventree_shop_quote_queue';
	const CRON_HOOK   = 'inventree_shop_retry_quotes';
	const MAX_ITEMS   = 25;

	/** @var array Errors and the submitted values, to redisplay the form. */
	private static $form_state = array();

	public static function init() {
		add_shortcode( 'inventree_quote_request', array( __CLASS__, 'shortcode' ) );
		add_action( 'init', array( __CLASS__, 'add_endpoint' ) );
		add_filter( 'query_vars', array( __CLASS__, 'query_vars' ) );
		add_filter( 'woocommerce_account_menu_items', array( __CLASS__, 'menu_item' ) );
		add_action( 'woocommerce_account_' . self::ENDPOINT . '_endpoint', array( __CLASS__, 'account_page' ) );
		add_action( 'template_redirect', array( __CLASS__, 'handle_post' ) );
		add_action( 'template_redirect', array( __CLASS__, 'maybe_render_label' ), 5 );
		add_action( 'woocommerce_single_product_summary', array( __CLASS__, 'custom_version_button' ), 35 );
		add_action( self::CRON_HOOK, array( __CLASS__, 'retry_queue' ) );
		if ( ! wp_next_scheduled( self::CRON_HOOK ) ) {
			wp_schedule_event( time() + 300, 'hourly', self::CRON_HOOK );
		}

		// Quote-only products: never buyable from the catalogue, only through their quote's order.
		add_filter( 'woocommerce_is_purchasable', array( __CLASS__, 'quote_only_not_purchasable' ), 10, 2 );
		add_filter( 'woocommerce_pay_order_product_is_purchasable', array( __CLASS__, 'allow_on_quote_order' ), 10, 3 );
		add_filter( 'woocommerce_pay_order_product_in_stock', array( __CLASS__, 'allow_on_quote_order' ), 10, 3 );
		add_action( 'template_redirect', array( __CLASS__, 'hide_quote_only_product_page' ) );
	}

	// ------------------------------------------------------------------ helpers

	public static function max_files() {
		return max( 1, (int) InvenTree_Shop_Settings::get( 'max_files', 20 ) );
	}

	public static function max_bytes() {
		return max( 1, (int) InvenTree_Shop_Settings::get( 'max_file_mb', 20 ) ) * 1024 * 1024;
	}

	public static function new_reference() {
		$chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
		$rand  = '';
		for ( $i = 0; $i < 4; $i++ ) {
			$rand .= $chars[ wp_rand( 0, strlen( $chars ) - 1 ) ];
		}
		return 'QR-' . gmdate( 'ymd' ) . '-' . $rand;
	}

	public static function account_url( $value = '', $args = array() ) {
		$url = wc_get_endpoint_url( self::ENDPOINT, $value, wc_get_page_permalink( 'myaccount' ) );
		return $args ? add_query_arg( $args, $url ) : $url;
	}

	private static function customer( $user_id ) {
		$user = get_userdata( $user_id );
		$name = trim( get_user_meta( $user_id, 'billing_first_name', true ) . ' ' . get_user_meta( $user_id, 'billing_last_name', true ) );
		return array(
			'id'      => (int) $user_id,
			'email'   => $user ? $user->user_email : '',
			'name'    => $name ? $name : ( $user ? $user->display_name : '' ),
			'phone'   => (string) get_user_meta( $user_id, 'billing_phone', true ),
			'company' => (string) get_user_meta( $user_id, 'billing_company', true ),
		);
	}

	/**
	 * Check one item as the customer submitted it. Returns a list of problems.
	 * Mirrors the rules InvenTree applies, so customers see them before sending.
	 */
	public static function item_problems( array $item, $position, $file_count, $phone ) {
		$problems = array();
		/* translators: %d: item number */
		$label = sprintf( __( 'Item %d', 'inventree-shop' ), $position );
		if ( (int) $item['quantity'] < 1 ) {
			$problems[] = $label . ': ' . __( 'quantity must be at least 1.', 'inventree-shop' );
		}
		if ( '' === $item['description'] && '' === $item['oem_part_number'] ) {
			$problems[] = $label . ': ' . __( 'describe it or give a part number.', 'inventree-shop' );
		}
		if ( in_array( $item['kind'], array( 'custom', 'copy' ), true ) && ! $file_count && ! $item['provide']['sample'] && ! $item['provide']['call'] ) {
			$problems[] = $label . ': ' . __( 'upload a layout drawing or photos, send us a sample, or ask us to call.', 'inventree-shop' );
		}
		if ( $item['provide']['call'] && '' === $phone ) {
			$problems[] = $label . ': ' . __( 'we need a phone number to call you.', 'inventree-shop' );
		}
		return $problems;
	}

	// ------------------------------------------------------------------ request form

	public static function shortcode() {
		if ( ! is_user_logged_in() ) {
			$login = wc_get_page_permalink( 'myaccount' );
			return '<div class="woocommerce-info inventree-quote-login">'
				. esc_html__( 'Please log in or create an account to request a quote.', 'inventree-shop' )
				. ' <a class="button" href="' . esc_url( add_query_arg( 'redirect_to', rawurlencode( get_permalink() ), $login ) ) . '">' . esc_html__( 'Log in / Register', 'inventree-shop' ) . '</a></div>';
		}
		if ( ! InvenTree_Link::configured() ) {
			return '<div class="woocommerce-error">' . esc_html__( 'Quote requests are not available right now.', 'inventree-shop' ) . '</div>';
		}

		wp_enqueue_script( 'inventree-quote-form', plugins_url( 'assets/quote-form.js', INVENTREE_SHOP_FILE ), array(), INVENTREE_SHOP_VERSION, true );
		wp_enqueue_style( 'inventree-quote-form', plugins_url( 'assets/quote-form.css', INVENTREE_SHOP_FILE ), array(), INVENTREE_SHOP_VERSION );

		$state    = self::$form_state;
		$customer = self::customer( get_current_user_id() );
		$old      = isset( $state['values'] ) ? $state['values'] : array();
		$items    = ! empty( $old['items'] ) ? $old['items'] : array( self::prefill_item() );
		$accept   = '.' . implode( ',.', self::FILE_TYPES );

		ob_start();
		if ( ! empty( $state['errors'] ) ) {
			echo '<div class="woocommerce-error" role="alert"><p>' . esc_html__( 'Please fix the following:', 'inventree-shop' ) . '</p><ul>';
			foreach ( $state['errors'] as $error ) {
				echo '<li>' . esc_html( $error ) . '</li>';
			}
			echo '</ul><p>' . esc_html__( 'Please attach your files again.', 'inventree-shop' ) . '</p></div>';
		}
		?>
		<form class="inventree-quote-form" method="post" enctype="multipart/form-data" novalidate>
			<?php wp_nonce_field( 'inventree_quote_submit', 'inventree_quote_nonce' ); ?>
			<input type="hidden" name="inventree_action" value="quote_submit">

			<div class="inventree-quote-items" data-max="<?php echo esc_attr( self::MAX_ITEMS ); ?>">
				<?php foreach ( array_values( $items ) as $index => $item ) : ?>
					<?php self::render_item( $index, $item, $accept ); ?>
				<?php endforeach; ?>
			</div>
			<p><button type="button" class="button inventree-add-item"><?php esc_html_e( '+ Add another item', 'inventree-shop' ); ?></button></p>

			<fieldset class="inventree-quote-details">
				<legend><?php esc_html_e( 'Your details', 'inventree-shop' ); ?></legend>
				<p class="form-row form-row-first"><label><?php esc_html_e( 'Company', 'inventree-shop' ); ?>
					<input type="text" class="input-text" name="company" value="<?php echo esc_attr( isset( $old['company'] ) ? $old['company'] : $customer['company'] ); ?>"></label></p>
				<p class="form-row form-row-last"><label><?php esc_html_e( 'Phone', 'inventree-shop' ); ?>
					<input type="tel" class="input-text" name="phone" value="<?php echo esc_attr( isset( $old['phone'] ) ? $old['phone'] : $customer['phone'] ); ?>"></label></p>
				<p class="form-row form-row-first"><label><?php esc_html_e( 'Needed by (optional)', 'inventree-shop' ); ?>
					<input type="date" class="input-text" name="required_by" value="<?php echo esc_attr( isset( $old['required_by'] ) ? $old['required_by'] : '' ); ?>"></label></p>
				<p class="form-row form-row-last"><label><?php esc_html_e( 'Delivery postcode', 'inventree-shop' ); ?>
					<input type="text" class="input-text" name="delivery_postcode" value="<?php echo esc_attr( isset( $old['delivery_postcode'] ) ? $old['delivery_postcode'] : get_user_meta( get_current_user_id(), 'shipping_postcode', true ) ); ?>"></label></p>
				<p class="form-row form-row-wide"><label><?php esc_html_e( 'Anything else we should know', 'inventree-shop' ); ?>
					<textarea class="input-text" name="notes" rows="3"><?php echo esc_textarea( isset( $old['notes'] ) ? $old['notes'] : '' ); ?></textarea></label></p>
			</fieldset>
			<p class="inventree-quote-limits">
				<?php
				/* translators: 1: max files, 2: max MB, 3: file types */
				echo esc_html( sprintf( __( 'Up to %1$d files, %2$d MB each: %3$s.', 'inventree-shop' ), self::max_files(), self::max_bytes() / 1048576, implode( ', ', self::FILE_TYPES ) ) );
				?>
			</p>
			<p><button type="submit" class="button alt"><?php esc_html_e( 'Send quote request', 'inventree-shop' ); ?></button></p>
		</form>
		<?php
		return ob_get_clean();
	}

	private static function prefill_item() {
		$item = array( 'kind' => 'custom', 'description' => '', 'oem_part_number' => '', 'machine' => '', 'quantity' => 1, 'reference_sku' => '', 'provide' => array( 'drawings' => true, 'sample' => false, 'call' => false ), 'call_time' => '' );
		$sku  = isset( $_GET['based_on'] ) ? sanitize_text_field( wp_unslash( $_GET['based_on'] ) ) : ''; // phpcs:ignore WordPress.Security.NonceVerification.Recommended
		if ( $sku ) {
			$product_id = wc_get_product_id_by_sku( $sku );
			$product    = $product_id ? wc_get_product( $product_id ) : null;
			if ( $product ) {
				$item['kind']          = 'copy';
				$item['reference_sku'] = $sku;
				/* translators: %s: product name */
				$item['description']   = sprintf( __( 'A custom version of %s:', 'inventree-shop' ), $product->get_name() ) . "\n";
			}
		}
		return $item;
	}

	private static function render_item( $index, array $item, $accept ) {
		$n    = $index + 1;
		$name = function ( $field ) use ( $index ) {
			return 'items[' . $index . '][' . $field . ']';
		};
		?>
		<fieldset class="inventree-quote-item" data-index="<?php echo esc_attr( $index ); ?>">
			<legend><?php /* translators: %d: item number */ echo esc_html( sprintf( __( 'Item %d', 'inventree-shop' ), $n ) ); ?></legend>
			<button type="button" class="inventree-remove-item" aria-label="<?php esc_attr_e( 'Remove this item', 'inventree-shop' ); ?>">&times;</button>
			<input type="hidden" name="<?php echo esc_attr( $name( 'reference_sku' ) ); ?>" value="<?php echo esc_attr( $item['reference_sku'] ); ?>">
			<p class="form-row form-row-first"><label><?php esc_html_e( 'What is it?', 'inventree-shop' ); ?>
				<select name="<?php echo esc_attr( $name( 'kind' ) ); ?>" class="inventree-kind">
					<option value="custom" <?php selected( $item['kind'], 'custom' ); ?>><?php esc_html_e( 'Custom harness', 'inventree-shop' ); ?></option>
					<option value="copy" <?php selected( $item['kind'], 'copy' ); ?>><?php esc_html_e( 'Copy of an existing harness', 'inventree-shop' ); ?></option>
					<option value="component" <?php selected( $item['kind'], 'component' ); ?>><?php esc_html_e( 'Component (connector, terminal, ...)', 'inventree-shop' ); ?></option>
				</select></label></p>
			<p class="form-row form-row-last"><label><?php esc_html_e( 'Quantity', 'inventree-shop' ); ?>
				<input type="number" min="1" class="input-text" name="<?php echo esc_attr( $name( 'quantity' ) ); ?>" value="<?php echo esc_attr( max( 1, (int) $item['quantity'] ) ); ?>"></label></p>
			<p class="form-row form-row-wide"><label><?php esc_html_e( 'Description', 'inventree-shop' ); ?>
				<textarea class="input-text" rows="3" name="<?php echo esc_attr( $name( 'description' ) ); ?>"><?php echo esc_textarea( $item['description'] ); ?></textarea></label></p>
			<p class="form-row form-row-first"><label><?php esc_html_e( 'OEM part number (if known)', 'inventree-shop' ); ?>
				<input type="text" class="input-text" name="<?php echo esc_attr( $name( 'oem_part_number' ) ); ?>" value="<?php echo esc_attr( $item['oem_part_number'] ); ?>"></label></p>
			<p class="form-row form-row-last"><label><?php esc_html_e( 'Machine (make / model / serial)', 'inventree-shop' ); ?>
				<input type="text" class="input-text" name="<?php echo esc_attr( $name( 'machine' ) ); ?>" value="<?php echo esc_attr( $item['machine'] ); ?>"></label></p>
			<div class="form-row form-row-wide inventree-provide">
				<p class="inventree-provide-help"><?php esc_html_e( 'How can we see what you need? (choose at least one for a harness)', 'inventree-shop' ); ?></p>
				<label><input type="checkbox" class="inventree-provide-drawings" name="<?php echo esc_attr( $name( 'provide' ) ); ?>[drawings]" value="1" <?php checked( ! empty( $item['provide']['drawings'] ) ); ?>> <?php esc_html_e( 'I\'ll upload layout drawing(s) or photos', 'inventree-shop' ); ?></label>
				<input type="file" class="inventree-files" name="item_files_<?php echo esc_attr( $index ); ?>[]" multiple accept="<?php echo esc_attr( $accept ); ?>">
				<label><input type="checkbox" name="<?php echo esc_attr( $name( 'provide' ) ); ?>[sample]" value="1" <?php checked( ! empty( $item['provide']['sample'] ) ); ?>> <?php esc_html_e( 'I\'ll send a sample (we\'ll give you a label)', 'inventree-shop' ); ?></label>
				<label><input type="checkbox" class="inventree-provide-call" name="<?php echo esc_attr( $name( 'provide' ) ); ?>[call]" value="1" <?php checked( ! empty( $item['provide']['call'] ) ); ?>> <?php esc_html_e( 'Call me to discuss', 'inventree-shop' ); ?></label>
				<input type="text" class="input-text inventree-call-time" placeholder="<?php esc_attr_e( 'Best time to call', 'inventree-shop' ); ?>" name="<?php echo esc_attr( $name( 'call_time' ) ); ?>" value="<?php echo esc_attr( $item['call_time'] ); ?>">
			</div>
		</fieldset>
		<?php
	}

	// ------------------------------------------------------------------ form handling

	public static function handle_post() {
		if ( 'POST' !== ( isset( $_SERVER['REQUEST_METHOD'] ) ? $_SERVER['REQUEST_METHOD'] : '' ) || empty( $_POST['inventree_action'] ) ) {
			return;
		}
		$action = sanitize_key( wp_unslash( $_POST['inventree_action'] ) );
		if ( 'quote_submit' === $action ) {
			self::handle_submit();
		} elseif ( 'quote_accept' === $action ) {
			self::handle_accept();
		}
	}

	/** Read the submitted form into clean values (no files). */
	public static function read_form( array $post ) {
		$values = array(
			'company'           => sanitize_text_field( wp_unslash( isset( $post['company'] ) ? $post['company'] : '' ) ),
			'phone'             => sanitize_text_field( wp_unslash( isset( $post['phone'] ) ? $post['phone'] : '' ) ),
			'required_by'       => preg_match( '/^\d{4}-\d{2}-\d{2}$/', isset( $post['required_by'] ) ? (string) $post['required_by'] : '' ) ? $post['required_by'] : '',
			'delivery_postcode' => sanitize_text_field( wp_unslash( isset( $post['delivery_postcode'] ) ? $post['delivery_postcode'] : '' ) ),
			'notes'             => sanitize_textarea_field( wp_unslash( isset( $post['notes'] ) ? $post['notes'] : '' ) ),
			'items'             => array(),
		);
		$raw_items = isset( $post['items'] ) && is_array( $post['items'] ) ? $post['items'] : array();
		foreach ( $raw_items as $index => $raw ) {
			if ( ! is_array( $raw ) || count( $values['items'] ) >= self::MAX_ITEMS ) {
				continue;
			}
			$provide = isset( $raw['provide'] ) && is_array( $raw['provide'] ) ? $raw['provide'] : array();
			$kind    = isset( $raw['kind'] ) ? sanitize_key( $raw['kind'] ) : 'custom';
			$values['items'][ (int) $index ] = array(
				'kind'            => in_array( $kind, self::KINDS, true ) ? $kind : 'custom',
				'description'     => sanitize_textarea_field( wp_unslash( isset( $raw['description'] ) ? $raw['description'] : '' ) ),
				'oem_part_number' => sanitize_text_field( wp_unslash( isset( $raw['oem_part_number'] ) ? $raw['oem_part_number'] : '' ) ),
				'machine'         => sanitize_text_field( wp_unslash( isset( $raw['machine'] ) ? $raw['machine'] : '' ) ),
				'quantity'        => max( 0, (int) ( isset( $raw['quantity'] ) ? $raw['quantity'] : 1 ) ),
				'reference_sku'   => sanitize_text_field( wp_unslash( isset( $raw['reference_sku'] ) ? $raw['reference_sku'] : '' ) ),
				'provide'         => array( 'drawings' => ! empty( $provide['drawings'] ), 'sample' => ! empty( $provide['sample'] ), 'call' => ! empty( $provide['call'] ) ),
				'call_time'       => sanitize_text_field( wp_unslash( isset( $raw['call_time'] ) ? $raw['call_time'] : '' ) ),
			);
		}
		return $values;
	}

	/** Uploaded files for one item index, checked. Returns [files, problems]. */
	public static function read_files( array $files_global, $index ) {
		$key = 'item_files_' . $index;
		$out = array();
		$bad = array();
		if ( empty( $files_global[ $key ] ) || ! is_array( $files_global[ $key ]['name'] ) ) {
			return array( $out, $bad );
		}
		$f = $files_global[ $key ];
		foreach ( $f['name'] as $i => $original ) {
			if ( '' === $original || UPLOAD_ERR_NO_FILE === (int) $f['error'][ $i ] ) {
				continue;
			}
			$name = sanitize_file_name( wp_basename( $original ) );
			$ext  = strtolower( pathinfo( $name, PATHINFO_EXTENSION ) );
			if ( UPLOAD_ERR_OK !== (int) $f['error'][ $i ] ) {
				/* translators: %s: file name */
				$bad[] = sprintf( __( '%s didn\'t upload (it may be too big).', 'inventree-shop' ), $name );
			} elseif ( ! in_array( $ext, self::FILE_TYPES, true ) ) {
				/* translators: %s: file name */
				$bad[] = sprintf( __( '%s: that type of file can\'t be uploaded.', 'inventree-shop' ), $name );
			} elseif ( (int) $f['size'][ $i ] > self::max_bytes() ) {
				/* translators: 1: file name, 2: MB */
				$bad[] = sprintf( __( '%1$s is larger than %2$d MB.', 'inventree-shop' ), $name, self::max_bytes() / 1048576 );
			} else {
				$out[] = array( 'name' => $name, 'path' => $f['tmp_name'][ $i ], 'size' => (int) $f['size'][ $i ] );
			}
		}
		return array( $out, $bad );
	}

	/** Build the signed payload and the file list InvenTree expects. */
	public static function build_payload( $reference, array $customer, array $values, array $files_by_index ) {
		$items = array();
		$files = array();
		$file_list = array();
		$position = 0;
		$n = 0;
		foreach ( $values['items'] as $index => $item ) {
			$position++;
			$items[] = array_merge( $item, array( 'position' => $position ) );
			foreach ( isset( $files_by_index[ $index ] ) ? $files_by_index[ $index ] : array() as $file ) {
				$upload      = 'file_' . ( $n++ );
				$file_list[] = array( 'field' => 'item_' . $position, 'upload' => $upload, 'name' => $file['name'], 'size' => $file['size'], 'sha256' => hash_file( 'sha256', $file['path'] ) );
				$files[]     = array( 'upload' => $upload, 'path' => $file['path'], 'name' => $file['name'] );
			}
		}
		$customer['phone']   = '' !== $values['phone'] ? $values['phone'] : $customer['phone'];
		$customer['company'] = '' !== $values['company'] ? $values['company'] : $customer['company'];
		return array(
			array(
				'reference'         => $reference,
				'customer'          => $customer,
				'required_by'       => $values['required_by'],
				'delivery_postcode' => $values['delivery_postcode'],
				'notes'             => $values['notes'],
				'items'             => $items,
				'files'             => $file_list,
			),
			$files,
		);
	}

	private static function handle_submit() {
		if ( ! is_user_logged_in() ) {
			return; // the shortcode shows the login prompt
		}
		if ( ! isset( $_POST['inventree_quote_nonce'] ) || ! wp_verify_nonce( sanitize_key( wp_unslash( $_POST['inventree_quote_nonce'] ) ), 'inventree_quote_submit' ) ) {
			self::$form_state = array( 'errors' => array( __( 'Your session expired - please try again.', 'inventree-shop' ) ) );
			return;
		}
		$values   = self::read_form( $_POST ); // phpcs:ignore WordPress.Security.ValidatedSanitizedInput.InputNotSanitized -- sanitised in read_form
		$customer = self::customer( get_current_user_id() );
		$phone    = '' !== $values['phone'] ? $values['phone'] : $customer['phone'];
		$errors   = array();
		$files_by_index = array();
		$total_files    = 0;

		if ( ! $values['items'] ) {
			$errors[] = __( 'Add at least one item.', 'inventree-shop' );
		}
		$position = 0;
		foreach ( $values['items'] as $index => $item ) {
			$position++;
			list( $files, $bad ) = self::read_files( $_FILES, $index );
			$files_by_index[ $index ] = $files;
			$total_files += count( $files );
			$errors = array_merge( $errors, $bad, self::item_problems( $item, $position, count( $files ), $phone ) );
		}
		if ( $total_files > self::max_files() ) {
			/* translators: %d: max files */
			$errors[] = sprintf( __( 'At most %d files per request.', 'inventree-shop' ), self::max_files() );
		}
		if ( $errors ) {
			self::$form_state = array( 'errors' => $errors, 'values' => $values );
			return;
		}

		$reference = self::new_reference();
		list( $payload, $files ) = self::build_payload( $reference, $customer, $values, $files_by_index );
		$result = InvenTree_Link::post_quote( $payload, $files );

		if ( ! is_wp_error( $result ) && 400 === $result['code'] ) {
			$detail = isset( $result['body']['detail'] ) ? $result['body']['detail'] : __( 'The request was not accepted.', 'inventree-shop' );
			self::$form_state = array( 'errors' => array( $detail ), 'values' => $values );
			return;
		}
		$queued = is_wp_error( $result ) || $result['code'] >= 300;
		if ( $queued ) {
			self::queue( $reference, $payload, $files );
		}
		self::send_received_emails( $payload, $queued );
		wp_safe_redirect( self::account_url( $reference, array( 'submitted' => 1 ) ) );
		exit;
	}

	// ------------------------------------------------------------------ retry queue

	private static function queue_dir() {
		$uploads = wp_upload_dir();
		$dir     = trailingslashit( $uploads['basedir'] ) . 'inventree-shop-queue';
		if ( ! is_dir( $dir ) ) {
			wp_mkdir_p( $dir );
			file_put_contents( $dir . '/.htaccess', "Require all denied\nDeny from all\n" ); // phpcs:ignore WordPress.WP.AlternativeFunctions
			file_put_contents( $dir . '/index.php', "<?php\n// Silence.\n" ); // phpcs:ignore WordPress.WP.AlternativeFunctions
		}
		return $dir;
	}

	/** Keep a request InvenTree couldn't take yet: files moved out of PHP's temp folder. */
	public static function queue( $reference, array $payload, array $files ) {
		$dir = trailingslashit( self::queue_dir() ) . sanitize_file_name( $reference );
		wp_mkdir_p( $dir );
		$kept = array();
		foreach ( $files as $file ) {
			$target = $dir . '/' . $file['upload'] . '-' . wp_generate_password( 8, false ) . '.bin';
			if ( is_uploaded_file( $file['path'] ) ? move_uploaded_file( $file['path'], $target ) : copy( $file['path'], $target ) ) {
				$kept[] = array_merge( $file, array( 'path' => $target ) );
			}
		}
		$queue = get_option( self::QUEUE_OPT, array() );
		$queue[ $reference ] = array( 'payload' => $payload, 'files' => $kept, 'attempts' => 0, 'queued' => time() );
		update_option( self::QUEUE_OPT, $queue, false );
	}

	public static function retry_queue() {
		$queue = get_option( self::QUEUE_OPT, array() );
		foreach ( $queue as $reference => $entry ) {
			$result = InvenTree_Link::post_quote( $entry['payload'], $entry['files'] );
			$done   = ! is_wp_error( $result ) && in_array( $result['code'], array( 200, 201 ), true );
			$failed = ! is_wp_error( $result ) && 400 === $result['code'];
			$entry['attempts']++;
			if ( $done || $failed || $entry['attempts'] >= 48 ) {
				foreach ( $entry['files'] as $file ) {
					wp_delete_file( $file['path'] );
				}
				@rmdir( dirname( isset( $entry['files'][0] ) ? $entry['files'][0]['path'] : '' ) ); // phpcs:ignore WordPress.PHP.NoSilencedErrors
				unset( $queue[ $reference ] );
				if ( ! $done ) {
					$why = $failed && isset( $result['body']['detail'] ) ? $result['body']['detail'] : 'InvenTree could not be reached for two days.';
					wp_mail( self::notify_email(), "Quote request {$reference} could not be sent to InvenTree", $why . "\n\n" . wp_json_encode( $entry['payload'], JSON_PRETTY_PRINT ) );
				}
			} else {
				$queue[ $reference ] = $entry;
			}
		}
		update_option( self::QUEUE_OPT, $queue, false );
	}

	// ------------------------------------------------------------------ emails

	public static function notify_email() {
		$email = InvenTree_Shop_Settings::get( 'notify_email', '' );
		return $email ? $email : get_option( 'admin_email' );
	}

	public static function send_email( $to, $subject, $heading, $html ) {
		if ( function_exists( 'WC' ) && WC()->mailer() ) {
			$mailer = WC()->mailer();
			return $mailer->send( $to, $subject, $mailer->wrap_message( $heading, $html ) );
		}
		return wp_mail( $to, $subject, wp_strip_all_tags( $html ) );
	}

	private static function send_received_emails( array $payload, $queued ) {
		$ref     = $payload['reference'];
		$samples = array_filter( $payload['items'], function ( $i ) { return ! empty( $i['provide']['sample'] ); } );
		$calls   = array_filter( $payload['items'], function ( $i ) { return ! empty( $i['provide']['call'] ); } );

		$html  = '<p>' . esc_html( sprintf( /* translators: %s: reference */ __( 'Thanks - we\'ve received your quote request %s.', 'inventree-shop' ), $ref ) ) . '</p>';
		$html .= '<ul>';
		foreach ( $payload['items'] as $item ) {
			$html .= '<li>' . esc_html( sprintf( '%d x %s', $item['quantity'], $item['description'] ? $item['description'] : $item['oem_part_number'] ) ) . '</li>';
		}
		$html .= '</ul>';
		if ( $samples ) {
			$html .= '<p>' . esc_html__( 'Please print your sample label and attach it to the parcel so we know it\'s yours:', 'inventree-shop' )
				. ' <a href="' . esc_url( self::account_url( $ref . '/label' ) ) . '">' . esc_html__( 'Sample label', 'inventree-shop' ) . '</a></p>';
		}
		if ( $calls ) {
			$html .= '<p>' . esc_html__( 'We\'ll give you a call to discuss.', 'inventree-shop' ) . '</p>';
		}
		$html .= '<p><a href="' . esc_url( self::account_url( $ref ) ) . '">' . esc_html__( 'View your request', 'inventree-shop' ) . '</a></p>';
		/* translators: %s: reference */
		self::send_email( $payload['customer']['email'], sprintf( __( 'Quote request %s received', 'inventree-shop' ), $ref ), __( 'Quote request received', 'inventree-shop' ), $html );

		$admin  = '<p>' . esc_html( sprintf( '%s - %s (%s) %s', $ref, $payload['customer']['name'], $payload['customer']['company'], $payload['customer']['phone'] ) ) . '</p>';
		$admin .= '<p>' . esc_html( sprintf( '%d item(s), %d sample(s), %d call(s), %d file(s).', count( $payload['items'] ), count( $samples ), count( $calls ), count( $payload['files'] ) ) ) . '</p>';
		if ( $queued ) {
			$admin .= '<p><strong>' . esc_html__( 'InvenTree could not be reached - it has been queued and will be retried hourly.', 'inventree-shop' ) . '</strong></p>';
		}
		$admin .= '<p>' . esc_html__( 'Open it in InvenTree: Dashboard > Quote Requests.', 'inventree-shop' ) . '</p>';
		/* translators: %s: reference */
		self::send_email( self::notify_email(), sprintf( __( 'New quote request %s', 'inventree-shop' ), $ref ), __( 'New quote request', 'inventree-shop' ), $admin );
	}

	// ------------------------------------------------------------------ My Account > Quotes

	public static function add_endpoint() {
		add_rewrite_endpoint( self::ENDPOINT, EP_ROOT | EP_PAGES );
	}

	public static function query_vars( $vars ) {
		$vars[] = self::ENDPOINT;
		return $vars;
	}

	public static function menu_item( $items ) {
		$new = array();
		foreach ( $items as $key => $label ) {
			$new[ $key ] = $label;
			if ( 'orders' === $key ) {
				$new[ self::ENDPOINT ] = __( 'Quotes', 'inventree-shop' );
			}
		}
		if ( ! isset( $new[ self::ENDPOINT ] ) ) {
			$new[ self::ENDPOINT ] = __( 'Quotes', 'inventree-shop' );
		}
		return $new;
	}

	/** This customer's quotes from InvenTree, or WP_Error. */
	public static function fetch_quotes( $user_id ) {
		$result = InvenTree_Link::post_json( 'quotes/customer/', array( 'customer_id' => (int) $user_id ) );
		if ( is_wp_error( $result ) ) {
			return $result;
		}
		if ( 200 !== $result['code'] ) {
			return new WP_Error( 'inventree_error', isset( $result['body']['detail'] ) ? $result['body']['detail'] : 'InvenTree error' );
		}
		return isset( $result['body']['quotes'] ) ? $result['body']['quotes'] : array();
	}

	private static function queued_for( $user_id ) {
		$out = array();
		foreach ( get_option( self::QUEUE_OPT, array() ) as $reference => $entry ) {
			if ( (int) $entry['payload']['customer']['id'] === (int) $user_id ) {
				$out[ $reference ] = $entry['payload'];
			}
		}
		return $out;
	}

	public static function money( $amount, $currency ) {
		return function_exists( 'wc_price' ) ? wc_price( $amount, array( 'currency' => $currency ) ) : esc_html( $currency . ' ' . $amount );
	}

	public static function account_page( $value = '' ) {
		$user_id = get_current_user_id();
		$value   = trim( (string) $value, '/' );
		$quotes  = self::fetch_quotes( $user_id );
		$queued  = self::queued_for( $user_id );

		if ( isset( $_GET['submitted'] ) ) { // phpcs:ignore WordPress.Security.NonceVerification.Recommended
			echo '<div class="woocommerce-message">' . esc_html__( 'Thanks - your quote request has been sent. We\'ve emailed you a copy.', 'inventree-shop' ) . '</div>';
		}

		if ( '' === $value ) {
			self::render_list( $quotes, $queued );
			return;
		}
		$reference = strtoupper( sanitize_text_field( $value ) );
		if ( isset( $queued[ $reference ] ) ) {
			self::render_queued( $queued[ $reference ] );
			return;
		}
		if ( is_wp_error( $quotes ) ) {
			echo '<div class="woocommerce-error">' . esc_html__( 'Your quotes can\'t be loaded right now - please try again shortly.', 'inventree-shop' ) . '</div>';
			return;
		}
		foreach ( $quotes as $quote ) {
			if ( $quote['reference'] === $reference ) {
				self::render_detail( $quote );
				return;
			}
		}
		echo '<div class="woocommerce-error">' . esc_html__( 'Quote not found.', 'inventree-shop' ) . '</div>';
	}

	private static function render_list( $quotes, array $queued ) {
		$new_url = InvenTree_Shop_Settings::quote_page_url();
		if ( $new_url ) {
			echo '<p><a class="button" href="' . esc_url( $new_url ) . '">' . esc_html__( 'Request a quote', 'inventree-shop' ) . '</a></p>';
		}
		if ( is_wp_error( $quotes ) ) {
			echo '<div class="woocommerce-error">' . esc_html__( 'Your quotes can\'t be loaded right now - please try again shortly.', 'inventree-shop' ) . '</div>';
			$quotes = array();
		}
		if ( ! $quotes && ! $queued ) {
			echo '<div class="woocommerce-info">' . esc_html__( 'You haven\'t requested any quotes yet.', 'inventree-shop' ) . '</div>';
			return;
		}
		echo '<table class="woocommerce-orders-table shop_table shop_table_responsive inventree-quotes"><thead><tr>';
		foreach ( array( __( 'Quote', 'inventree-shop' ), __( 'Requested', 'inventree-shop' ), __( 'Items', 'inventree-shop' ), __( 'Status', 'inventree-shop' ), __( 'Total (ex GST)', 'inventree-shop' ), '' ) as $h ) {
			echo '<th>' . esc_html( $h ) . '</th>';
		}
		echo '</tr></thead><tbody>';
		foreach ( $queued as $ref => $payload ) {
			echo '<tr><td>' . esc_html( $ref ) . '</td><td></td><td>' . esc_html( count( $payload['items'] ) ) . '</td><td>' . esc_html__( 'Sending', 'inventree-shop' ) . '</td><td></td><td></td></tr>';
		}
		foreach ( $quotes as $q ) {
			echo '<tr>';
			echo '<td><a href="' . esc_url( self::account_url( $q['reference'] ) ) . '">' . esc_html( $q['reference'] ) . '</a></td>';
			echo '<td>' . esc_html( date_i18n( wc_date_format(), strtotime( $q['created_at'] ) ) ) . '</td>';
			echo '<td>' . esc_html( count( $q['items'] ) ) . '</td>';
			echo '<td>' . esc_html( $q['status_label'] ) . ( $q['can_accept'] ? ' <mark class="inventree-ready">' . esc_html__( 'ready to accept', 'inventree-shop' ) . '</mark>' : '' ) . '</td>';
			echo '<td>' . ( null !== $q['total'] ? wp_kses_post( self::money( $q['total'], $q['currency'] ) ) : '' ) . '</td>';
			echo '<td><a class="button view" href="' . esc_url( self::account_url( $q['reference'] ) ) . '">' . esc_html__( 'View', 'inventree-shop' ) . '</a></td>';
			echo '</tr>';
		}
		echo '</tbody></table>';
	}

	private static function render_queued( array $payload ) {
		echo '<h2>' . esc_html( $payload['reference'] ) . '</h2>';
		echo '<p>' . esc_html__( 'We\'ve got your request and it\'s on its way to our team.', 'inventree-shop' ) . '</p>';
		self::render_label_link( $payload['reference'], $payload['items'] );
	}

	private static function render_label_link( $reference, array $items ) {
		$has_sample = false;
		foreach ( $items as $item ) {
			$has_sample = $has_sample || ! empty( $item['sending_sample'] ) || ! empty( $item['provide']['sample'] );
		}
		if ( $has_sample ) {
			echo '<p><a class="button" target="_blank" href="' . esc_url( self::account_url( $reference . '/label' ) ) . '">' . esc_html__( 'Print sample label', 'inventree-shop' ) . '</a></p>';
		}
	}

	private static function render_detail( array $q ) {
		echo '<h2>' . esc_html( $q['reference'] ) . ' <small>' . esc_html( $q['status_label'] ) . '</small></h2>';
		if ( $q['valid_until'] && 'quoted' === $q['status'] ) {
			/* translators: %s: date */
			echo '<p>' . esc_html( sprintf( __( 'This quote is valid until %s.', 'inventree-shop' ), date_i18n( wc_date_format(), strtotime( $q['valid_until'] ) ) ) ) . '</p>';
		}
		if ( 'declined' === $q['status'] && $q['decline_reason'] ) {
			echo '<div class="woocommerce-info">' . esc_html( $q['decline_reason'] ) . '</div>';
		}
		self::render_label_link( $q['reference'], $q['items'] );

		echo '<h3>' . esc_html__( 'What you asked for', 'inventree-shop' ) . '</h3><ol class="inventree-quote-request-items">';
		foreach ( $q['items'] as $item ) {
			echo '<li><strong>' . esc_html( $item['quantity'] . ' x ' . $item['kind_label'] ) . '</strong> ' . esc_html( $item['description'] );
			$bits = array();
			if ( $item['oem_part_number'] ) {
				$bits[] = $item['oem_part_number'];
			}
			if ( $item['sending_sample'] ) {
				$bits[] = $item['sample_received'] ? __( 'sample received', 'inventree-shop' ) : __( 'sample to send', 'inventree-shop' );
			}
			if ( $item['wants_call'] ) {
				$bits[] = $item['called'] ? __( 'we\'ve called', 'inventree-shop' ) : __( 'we\'ll call you', 'inventree-shop' );
			}
			if ( $bits ) {
				echo ' <span class="inventree-muted">(' . esc_html( implode( ', ', $bits ) ) . ')</span>';
			}
			if ( 'cannot' === $item['state'] ) {
				echo '<br><em>' . esc_html__( 'We can\'t supply this:', 'inventree-shop' ) . ' ' . esc_html( $item['cannot_reason'] ) . '</em>';
			}
			echo '</li>';
		}
		echo '</ol>';

		if ( empty( $q['lines'] ) ) {
			if ( in_array( $q['status'], array( 'new', 'waiting_sample', 'call', 'reviewing' ), true ) ) {
				echo '<p>' . esc_html__( 'We\'re working on your quote and will email you when it\'s ready.', 'inventree-shop' ) . '</p>';
			}
			return;
		}

		echo '<h3>' . esc_html__( 'Your quote', 'inventree-shop' ) . '</h3>';
		echo '<form method="post" class="inventree-quote-accept">';
		wp_nonce_field( 'inventree_quote_accept_' . $q['reference'], 'inventree_quote_nonce' );
		echo '<input type="hidden" name="inventree_action" value="quote_accept"><input type="hidden" name="reference" value="' . esc_attr( $q['reference'] ) . '">';
		echo '<table class="shop_table inventree-quote-lines"><thead><tr>';
		echo $q['can_accept'] ? '<th>' . esc_html__( 'Accept', 'inventree-shop' ) . '</th>' : '';
		foreach ( array( __( 'Item', 'inventree-shop' ), __( 'Qty', 'inventree-shop' ), __( 'Unit (ex GST)', 'inventree-shop' ), __( 'Total (ex GST)', 'inventree-shop' ), __( 'Lead time', 'inventree-shop' ) ) as $h ) {
			echo '<th>' . esc_html( $h ) . '</th>';
		}
		echo '</tr></thead><tbody>';
		foreach ( $q['lines'] as $line ) {
			echo '<tr>';
			if ( $q['can_accept'] ) {
				echo '<td><input type="checkbox" name="line_ids[]" value="' . esc_attr( $line['id'] ) . '" checked aria-label="' . esc_attr__( 'Accept this line', 'inventree-shop' ) . '"></td>';
			}
			echo '<td>' . esc_html( $line['description'] ) . '</td><td>' . esc_html( $line['quantity'] ) . '</td>';
			echo '<td>' . wp_kses_post( self::money( $line['unit_price'], $q['currency'] ) ) . '</td><td>' . wp_kses_post( self::money( $line['total'], $q['currency'] ) ) . '</td>';
			echo '<td>' . esc_html( $line['lead_time'] ) . '</td></tr>';
		}
		echo '</tbody><tfoot><tr><th colspan="' . ( $q['can_accept'] ? 4 : 3 ) . '">' . esc_html__( 'Total (ex GST)', 'inventree-shop' ) . '</th><td colspan="2">' . wp_kses_post( self::money( $q['total'], $q['currency'] ) ) . '</td></tr></tfoot></table>';
		if ( $q['can_accept'] ) {
			echo '<p class="inventree-muted">' . esc_html__( 'Untick anything you don\'t want. GST and delivery are added at checkout. Trade account customers can choose "Charge to my account".', 'inventree-shop' ) . '</p>';
			echo '<p><button type="submit" class="button alt">' . esc_html__( 'Accept and continue to payment', 'inventree-shop' ) . '</button></p>';
		}
		echo '</form>';
	}

	private static function handle_accept() {
		if ( ! is_user_logged_in() ) {
			return;
		}
		$reference = isset( $_POST['reference'] ) ? strtoupper( sanitize_text_field( wp_unslash( $_POST['reference'] ) ) ) : '';
		if ( ! isset( $_POST['inventree_quote_nonce'] ) || ! wp_verify_nonce( sanitize_key( wp_unslash( $_POST['inventree_quote_nonce'] ) ), 'inventree_quote_accept_' . $reference ) ) {
			wc_add_notice( __( 'Your session expired - please try again.', 'inventree-shop' ), 'error' );
			return;
		}
		$line_ids = isset( $_POST['line_ids'] ) ? array_map( 'absint', (array) wp_unslash( $_POST['line_ids'] ) ) : array();
		$result   = InvenTree_Link::post_json( 'quotes/accept/', array( 'customer_id' => get_current_user_id(), 'reference' => $reference, 'line_ids' => $line_ids ) );
		if ( is_wp_error( $result ) || 200 !== $result['code'] || empty( $result['body']['payment_url'] ) ) {
			$detail = ! is_wp_error( $result ) && isset( $result['body']['detail'] ) ? $result['body']['detail'] : __( 'Your quote couldn\'t be accepted right now - please try again shortly.', 'inventree-shop' );
			wc_add_notice( $detail, 'error' );
			return;
		}
		wp_safe_redirect( $result['body']['payment_url'] ); // same site: WooCommerce's pay-for-order page
		exit;
	}

	// ------------------------------------------------------------------ sample label

	public static function maybe_render_label() {
		global $wp;
		if ( ! is_user_logged_in() || ! isset( $wp->query_vars[ self::ENDPOINT ] ) ) {
			return;
		}
		$value = trim( (string) $wp->query_vars[ self::ENDPOINT ], '/' );
		if ( ! preg_match( '#^(QR-[A-Z0-9-]+)/label$#i', $value, $m ) ) {
			return;
		}
		$reference = strtoupper( $m[1] );
		$quote     = null;
		$queued    = self::queued_for( get_current_user_id() );
		if ( isset( $queued[ $reference ] ) ) {
			$quote = array( 'reference' => $reference, 'items' => $queued[ $reference ]['items'] );
		} else {
			$quotes = self::fetch_quotes( get_current_user_id() );
			foreach ( is_wp_error( $quotes ) ? array() : $quotes as $q ) {
				if ( $q['reference'] === $reference ) {
					$quote = $q;
				}
			}
		}
		if ( ! $quote ) {
			wp_die( esc_html__( 'Quote not found.', 'inventree-shop' ), '', array( 'response' => 404 ) );
		}
		echo self::label_html( $quote, self::customer( get_current_user_id() ) ); // phpcs:ignore WordPress.Security.EscapeOutput.OutputNotEscaped -- escaped inside
		exit;
	}

	public static function label_html( array $quote, array $customer ) {
		$address = InvenTree_Shop_Settings::get( 'sample_address', '' );
		$rows    = '';
		foreach ( $quote['items'] as $index => $item ) {
			$sample = ! empty( $item['sending_sample'] ) || ! empty( $item['provide']['sample'] );
			if ( $sample ) {
				$pos   = isset( $item['position'] ) ? $item['position'] : $index + 1;
				$rows .= '<li>' . esc_html( sprintf( 'Item %d: %d x %s', $pos, $item['quantity'], $item['description'] ? $item['description'] : $item['oem_part_number'] ) ) . '</li>';
			}
		}
		return '<!doctype html><html><head><meta charset="utf-8"><title>' . esc_html( $quote['reference'] ) . '</title>'
			. '<style>body{font-family:Arial,sans-serif;margin:24px}.label{border:3px dashed #000;padding:24px;max-width:640px}'
			. '.ref{font-size:44px;font-weight:bold;letter-spacing:2px;margin:8px 0}.to{font-size:20px;white-space:pre-line;margin:16px 0}'
			. '@media print{.noprint{display:none}}</style></head><body>'
			. '<p class="noprint"><button onclick="window.print()">' . esc_html__( 'Print', 'inventree-shop' ) . '</button> '
			. esc_html__( 'Cut this out and attach it to your sample parcel.', 'inventree-shop' ) . '</p>'
			. '<div class="label"><div>' . esc_html__( 'SAMPLE FOR QUOTE', 'inventree-shop' ) . '</div><div class="ref">' . esc_html( $quote['reference'] ) . '</div>'
			. ( $address ? '<div class="to"><strong>' . esc_html__( 'To:', 'inventree-shop' ) . '</strong>' . "\n" . esc_html( $address ) . '</div>' : '' )
			. '<div><strong>' . esc_html__( 'From:', 'inventree-shop' ) . '</strong> ' . esc_html( trim( $customer['name'] . ' - ' . $customer['company'], ' -' ) ) . ' ' . esc_html( $customer['phone'] ) . '</div>'
			. ( $rows ? '<ul>' . $rows . '</ul>' : '' ) . '</div></body></html>';
	}

	// ------------------------------------------------------------------ product pages

	public static function custom_version_button() {
		global $product;
		if ( ! $product || 'no' === get_option( 'inventree_shop_custom_button', 'yes' ) || '1' === (string) $product->get_meta( '_inventree_quote_only' ) ) {
			return;
		}
		$url = InvenTree_Shop_Settings::quote_page_url( array( 'based_on' => $product->get_sku() ) );
		if ( $url ) {
			echo '<p class="inventree-custom-version"><a class="button" href="' . esc_url( $url ) . '">' . esc_html__( 'Request a custom version', 'inventree-shop' ) . '</a></p>';
		}
	}

	public static function quote_only_not_purchasable( $purchasable, $product ) {
		if ( $product && '1' === (string) $product->get_meta( '_inventree_quote_only' ) ) {
			return false;
		}
		return $purchasable;
	}

	/** On the pay-for-order page of a quote's order, its products can be paid for. */
	public static function allow_on_quote_order( $allowed, $product, $order ) {
		if ( $order && $order->get_meta( 'inventree_quote' ) ) {
			return true;
		}
		return $allowed;
	}

	public static function hide_quote_only_product_page() {
		if ( function_exists( 'is_product' ) && is_product() && ! current_user_can( 'edit_products' ) ) {
			$product = wc_get_product( get_queried_object_id() );
			if ( $product && '1' === (string) $product->get_meta( '_inventree_quote_only' ) ) {
				global $wp_query;
				$wp_query->set_404();
				status_header( 404 );
			}
		}
	}
}
